# 002 — Payment integrity follow-up

Status: Phase 0 and Phase 1 complete; Phase 2 IN PROGRESS (owner-authorized 2026-08-11)

Owner surfaces: `app/core/payments/`, payment callers, the payment/clearing boundary

Evidence ledger: [phase0-evidence-map.md](phase0-evidence-map.md)

Delivery sequence: [plan.md](plan.md)
Executable backlog: [tasks.md](tasks.md)

## 1. Problem and measured need

The payment engine protects route segments with PostgreSQL transaction advisory
locks, but its lock identity is directed while the mutable debt resource is not.
For one equivalent, both `A -> B` and `B -> A` flows can update the two reciprocal
`Debt` rows and net them against each other. The current lock key hashes
`equivalent + from + to`, so the reverse flow receives a different key.

Phase 0 reproduced three distinct facts on PostgreSQL 16:

1. a session holding the `A -> B` advisory key does not block another session
   from acquiring the `B -> A` key;
2. inverse multi-segment `commit=True` payments can enter a real row-level
   deadlock (`40P01`), after which whole-UoW retry preserves exactly-once effects;
3. inverse lock accumulation across multiple `commit=False` calls in two outer
   transactions can repeat `40P01` until one staged operation exhausts every
   configured attempt. The temporary characterization used four zero-delay
   attempts to expose persistence; the production default is three. A savepoint
   rollback cannot release an advisory transaction lock acquired before it.

This is a confirmed P2 serialization and liveness defect. The checked production
default, PostgreSQL `SERIALIZABLE`, prevented a partial or duplicated monetary
effect in the reproducer. Phase 0 therefore does not classify it as a confirmed
P1 data-integrity loss. The absence of corruption in this finite run is not a
proof for untested deployment isolation levels or cancellation boundaries.

## 2. Current behavior

### 2.1 Payment lock identity and ordering

- `app/core/payments/engine.py:82-93` derives a directed SHA-256 key from the
  equivalent, `from` participant and `to` participant.
- `tests/unit/test_payment_engine_advisory_lock_key.py:13-18` explicitly requires
  the reverse key to differ.
- `app/core/payments/engine.py:115-150` deduplicates and sorts only the already
  directed keys presented to one acquisition call.
- `app/core/payments/engine.py:152-164` applies a decreasing advisory-lock budget.
- prepare, prepare-routes, commit and abort all use the same directed segment-key
  mechanism (`engine.py:359-580`, `582-822`, `824-1180`, `1409-1629`).

Sorting is locally correct but is not a global transaction order. A transaction
may retain key `9` from one staged call and later request key `2`, while a sibling
transaction retains `2` and requests `9`.

### 2.2 Shared monetary resource

`app/core/payments/engine.py:1182-1255` reads and mutates both reciprocal debt
rows while applying one flow. `app/core/payments/router.py:267-335` likewise
derives directed capacity from both debt directions. The serialization resource
is therefore `(equivalent, unordered participant pair)`, even though business
flow and audit direction remain directed.

### 2.3 Transaction ownership and retry

- Public payment calls own their transaction; internal staged calls pass
  `commit=False` (`app/core/payments/service.py:160-250`).
- Whole-UoW retry handles `40P01` and `40001`. PostgreSQL may expose the same
  invisible concurrent Debt insert after a SERIALIZABLE advisory-lock wait as
  either `40001` or `23505`; commit retries `23505` only for the exact Debt
  business-key INSERT/constraint. The staged variant uses a savepoint and
  deliberately does not roll back the caller's outer transaction
  (`app/core/payments/engine.py:333-500`).
- Real simulator payment batching calls the staged service from
  `app/core/simulator/real_payments_executor.py:369`.
- The service owns a total timeout and shielded abort paths
  (`app/core/payments/service.py:462-868`).
- PostgreSQL defaults to `SERIALIZABLE` in `app/config.py:68` and is applied by
  `app/db/session.py:58-63`.

The staged retry contract is insufficient for an ordering deadlock involving a
lock retained outside the current savepoint. Retrying only the second acquisition
recreates the same wait graph.

### 2.4 Clearing boundary

Clearing already treats a participant pair as unordered when scanning prepared
payments (`app/core/clearing/service.py:447-472`), but it does not acquire the
payment advisory key. It locks `Debt` rows and only then takes a PrepareLock
snapshot (`service.py:827-876`). This leaves a time-of-check/time-of-use window in
which a payment can prepare after the snapshot. Existing PostgreSQL coverage
proves final row/version preservation for one schedule, not a shared lock domain.

## 3. Intended behavior

The active contracts require:

- creditor-to-debtor trustline direction remains unchanged;
- route capacity cannot be oversubscribed;
- prepare, commit and abort remain idempotent by `tx_id`;
- a timeout, retry or cancellation cannot double-apply or partially apply money;
- prepared segments are not cleared underneath an active payment
  (`docs/ru/simulator/backend/payment-integration.md:70`);
- transaction ownership remains explicit: `commit=False` never commits or rolls
  back the caller's outer transaction.

No active domain document intentionally defines reverse directions as independent
serialization resources. The reverse-key inequality is an implementation-era
test expectation, not a sufficient monetary-resource contract.

## 4. Optimal bounded target

### 4.1 Resource identity

All payment transitions that can mutate or reserve reciprocal debt capacity MUST
serialize on a stable identity equivalent to:

`(equivalent_id, min(participant_a, participant_b), max(participant_a, participant_b))`.

This canonicalization applies only to concurrency control. Persisted flow
direction, trustline meaning, audit payloads, routing semantics and wire schemas
remain directed and unchanged.

### 4.2 Transaction-wide acquisition order

A transaction that stages multiple payment operations MUST NOT incrementally
accumulate unordered pair locks in an order that can conflict with another outer
transaction. Before its first monetary mutation, the owner must either:

- acquire the complete canonical pair lock set in one deterministic order; or
- acquire a stable, documented coarser owner lock that covers the staged batch.

Phase 1 must choose the smallest mechanism supported by caller inventory and
PostgreSQL evidence. A savepoint-only retry is not an acceptable fix for a lock
retained by the outer transaction.

### 4.3 Clearing interlock

Clearing and payments that touch the same reciprocal debt resource MUST have one
documented serialization boundary. Phase 2 may implement a shared canonical lock
or an equally strong row-lock/revalidation protocol, but it must prove that a
payment cannot become newly prepared between clearing's conflict decision and
the monetary mutation.

### 4.4 Failure semantics

- `40P01`/`40001` before a transaction-owned commit: retry the whole owned UoW.
- A commit-owned invisible concurrent Debt insert may surface as `40001` or
  `23505`: retry the whole UoW. The `23505` exception is retryable only when both
  the statement is `INSERT INTO debts` and the constraint is
  `uq_debts_debtor_creditor_equivalent`; all other unique violations fail closed.
- `40P01`/`40001` in staged work: retry only when the conflicting lock set is
  contained in the savepoint; otherwise fail deterministically to the outer owner
  or restart the whole outer UoW. Commit-only `23505` is not retryable for the
  staged `commit_nocommit` operation.
- `55P03`: map to the existing bounded timeout contract.
- Cancellation: propagate after rollback/cleanup owned by the applicable layer.
- Ambiguous commit acknowledgement: resolve from durable state before publishing
  success/failure or retrying a monetary effect.

## 5. Protected invariants

1. **Atomicity:** no partial route effect is durable.
2. **Exactly once:** one committed `tx_id` produces one payment audit effect.
3. **Conservation:** reciprocal debt netting preserves the domain balance.
4. **Capacity:** concurrent routes never reserve more than available capacity.
5. **State machine:** transaction and PrepareLock transitions remain valid and
   restart-safe.
6. **Ownership:** inner staged code does not decide the outer commit/rollback.
7. **Bounded liveness:** waits and retries have a finite owner and budget.
8. **Direction:** canonical lock identity does not reverse creditor/debtor meaning.

## 6. Scope

### Phase 0 scope

- source, history and call-site audit;
- real PostgreSQL characterization;
- specification, phased plan and anti-regression matrix;
- internal adversarial and external read-only review of the governance diff.

Phase 0 MUST NOT change product code, tests, migrations, OpenAPI, UI, fixtures or
historical migrations.

### Program scope after owner authorization

- canonical payment pair serialization;
- transaction-wide ordering for staged multi-payment owners;
- the directly overlapping clearing/payment serialization boundary;
- targeted unit and PostgreSQL concurrency evidence;
- stable domain documentation and closeout evidence.

### Non-goals

- changing routing or trustline direction;
- changing REST/SSE payloads;
- replacing SQLAlchemy transaction ownership;
- broad clearing, simulator, recovery or audit refactoring;
- tuning retry counts to make one schedule pass;
- supporting a non-PostgreSQL advisory-lock implementation.

## 7. Dependencies and owners

- Payment engine: lock identity, transition protocol and retry semantics.
- Payment service and real simulator executor: outer UoW ownership for staged work.
- Clearing service: only the shared pair conflict boundary.
- PostgreSQL test harness: disposable isolated database and deterministic barriers.
- Stable RU payment/decision documentation: target contract after implementation.

No API, schema, migration or UI dependency is currently expected. Discovery of
one reopens planning before implementation.

### 7.1 Transaction-owner inventory

| Caller | Engine operation | Commit mode | Transaction owner |
|---|---|---|---|
| Public/internal `PaymentService` | prepare/commit/abort | `commit=True` | payment service/engine UoW |
| Real simulator payment batch | staged payment | `commit=False` | real tick payment coordinator/executor |
| Admin transaction abort | abort plus audit | `commit=False` | Admin endpoint session (`app/api/v1/admin.py:1026-1033`) |
| Expired PrepareLock recovery | abort | `commit=True` default | recovery item (`app/core/recovery.py:107-122`) |
| Stale-payment recovery | abort | `commit=True` default | recovery item (`app/core/recovery.py:212-226`) |
| Clearing service | clearing mutation | separate raw commit | clearing service; no shared payment advisory identity |

Phase 1 must re-run this inventory before selecting a compatibility or staged
acquisition mechanism; every owner that can coexist during rollout participates
in the old/new concurrency matrix.

## 8. Anti-regression acceptance

The delivery phases must prove, with deterministic barriers rather than sleeps:

- direct `A -> B` and `B -> A` acquisitions conflict on one canonical resource;
- inverse single- and multi-segment prepare/commit cannot bypass serialization;
- both route start orders have the same bounded outcome;
- same-direction bottleneck and same-`tx_id` idempotency coverage remain green;
- concurrent Debt insert after a SERIALIZABLE lock wait has bounded real
  PostgreSQL characterization for the server-selected `40001`/narrow `23505`
  outcome in addition to predicate anti-vacuum guards;
- staged multi-payment owners cannot exhaust savepoint retry on retained locks;
- timeout and cancellation release owned locks without partial/double effect;
- final reciprocal debts, trust limits, transaction states, PrepareLock count and
  audit count are asserted;
- clearing cannot pass an empty prepared-pair snapshot and then race a new prepare;
- SQLite/unit results are never reported as PostgreSQL lock evidence.

## 9. Rollout and rollback

Delivery is split into reversible commits by owner surface. No data migration is
expected because advisory keys are transaction-scoped and not persisted. Deploy
only after the PostgreSQL matrix passes on the exact commit.

Rollback is `git revert` of the affected delivery commit. During mixed-version
rollout, old and new processes would use different advisory identities; therefore
rolling deployment is unsafe unless the chosen implementation supplies an
explicit compatibility bridge. A valid bridge must acquire the canonical key and
**both** legacy directional keys for every unordered pair in one globally defined
order; acquiring only the caller's directed legacy key still permits an old
reverse-direction worker to bypass it. The old/new × both-directions PostgreSQL
matrix must include service, staged, Admin-abort and recovery owners. Otherwise
Phase 1 requires coordinated quiescence, and Phase 2 rollback inherits that
deployment constraint.

## 10. Stop rule

Phase 0 closes when evidence, specification, plan and tasks have passed internal
adversarial review and the required external governance review, with no unresolved
P1 in this program's scope. It does not authorize Phase 1.

The program closes after Phase 2 only when the exact-head PostgreSQL acceptance
matrix is green, stable docs match runtime, no P1/P2 remains in scope, and all
unverified paths are explicitly accepted or assigned to a new owner-approved
program.

## 11. Phase 1 implementation evidence (append-only)

### 2026-08-11 — P100

- Перед правкой current anchors подтверждены на `39f960e`: направленный key material остаётся в
  `app/core/payments/engine.py:82-93`, acquisition сортирует лишь уже полученные ключи на
  `:115-150`, а `tests/unit/test_payment_engine_advisory_lock_key.py:13-18` требует reverse-key
  inequality. Wave 2 меняла в `engine.py` только audit exception blocks; finding подтверждён.
- Добавлен реальный PostgreSQL 16 reproducer
  `tests/integration/test_payment_pair_advisory_locks_postgres.py:1-63`. Holder берёт `A→B`, waiter
  просит `B→A` с bounded DB lock timeout; ожидаемый target — PostgreSQL `55P03` на одной advisory
  identity. Никакой synthetic `DBAPIError` не создаётся.
- Canonical RED на отдельной проверенной БД `geov0_test_wave3`:
  `DEBUG=false; .\\scripts\\verify_local.ps1 -TaskSlug wave3_p100_red -BackendOnly -BackendMarker postgres -BackendSelector tests/integration/test_payment_pair_advisory_locks_postgres.py`
  — exit `1`, `1 failed`, exact `Failed: DID NOT RAISE <class 'sqlalchemy.exc.DBAPIError'>`.
- До P104 тест помечен `xfail(strict=True)` (`:22-25`): target improvement обязан дать XPASS.
  Та же canonical команда с `-TaskSlug wave3_p100` — exit `0`, `1 xfailed`; pinned Ruff и
  `git diff --check` — exit `0`. Test commit: `678d300`.

### 2026-08-11 — P101

- Перед правкой подтверждён владеющий путь: `app/core/payments/engine.py:899-906` вычисляет ключи
  `commit()` из направленных persisted flows, а `:1031-1068` затем применяет оба сегмента.
  `engine.py:82-93` всё ещё различает `(A,B)` и `(B,A)`, поэтому finding подтверждён без anchor
  drift.
- Добавлен параметризованный реальный PostgreSQL 16 test
  `tests/integration/test_payment_inverse_multisegment_postgres.py:231-388`: оба порядка holder
  (`A→B→C` и `C→B→A`), наблюдаемый через `pg_locks` advisory-wait barrier (`:205-224`), retries
  отключены как источник false-green (`:267-268`). Target после P104 проверит два `COMMITTED`,
  направленный net debt `1.00000000` на обоих сегментах, неизменные четыре trust limits, ноль
  `PrepareLock` и ровно две payment audit rows (`:322-388`).
- Первый canonical RED attempt `wave3_p101_red` — exit `1`, `2 failed` на test-fixture
  `ForeignKeyViolationError`: ORM не имел relationship для упорядочивания `Transaction` и
  `PrepareLock` в одном flush. Harness исправлен явным flush parent rows
  (`test_payment_inverse_multisegment_postgres.py:115-118`); это не product failure.
- Исправленный canonical RED:
  `DEBUG=false; TEST_DATABASE_URL=postgresql+asyncpg://geo:geo@localhost:55433/geov0_test_wave3; GEO_TEST_ALLOW_DB_RESET=1; .\scripts\verify_local.ps1 -TaskSlug wave3_p101_red2 -BackendOnly -BackendMarker postgres -BackendSelector tests/integration/test_payment_inverse_multisegment_postgres.py`
  — exit `1`, `2 failed`; оба start order дали exact
  `AssertionError: inverse route bypassed the holder's pair locks` на `:304`.
- До P104 оба параметра помечены `xfail(strict=True)` (`:231-234`). Canonical повтор после
  последнего test change с `-TaskSlug wave3_p101_final` — exit `0`, `2 xfailed`; pinned
  `ruff==0.1.14` и `git diff --check` — exit `0`. Test commit:
  `6a12c47980b05d3aa9b3c95b2feae13829479219`.

### 2026-08-11 — P102

- Перед правкой staged-owner anchors подтверждены: public staged entrypoint передаёт
  `commit=False` в `app/core/payments/service.py:304-333`; real executor держит один outer session
  и вызывает его под savepoint на `app/core/simulator/real_payments_executor.py:365-379`.
  `PaymentEngine._run_uow_with_retry` на `engine.py:263-348` откатывает лишь текущий savepoint, не
  transaction advisory lock успешного предыдущего staged call; finding подтверждён.
- Добавлен реальный PostgreSQL 16 regression
  `tests/integration/test_payment_staged_multicall_postgres.py:195-345`. Две независимые
  `SERIALIZABLE` outer sessions сначала сохраняют противоположный порядок pair locks через
  успешные `commit=False`, затем одновременно просят удерживаемую другой стороной пару
  (`:203-280`). Используется production retry count `3`, который test проверяет, но не подменяет;
  для детерминизма обнулены только задержки (`:241-251`). Test не создаёт `DBAPIError` и не
  подменяет SQLSTATE.
- Canonical RED на отдельной проверенной БД `geov0_test_wave3`:
  `DEBUG=false; TEST_DATABASE_URL=postgresql+asyncpg://geo:geo@localhost:55433/geov0_test_wave3; GEO_TEST_ALLOW_DB_RESET=1; .\scripts\verify_local.ps1 -TaskSlug wave3_p102_red -BackendOnly -BackendMarker postgres -BackendSelector tests/integration/test_payment_staged_multicall_postgres.py`
  — exit `1`, `1 failed`; exact assertion
  `staged owner exhausted retry while retaining its first pair lock: sqlstates=['40P01']`.
  PostgreSQL вернул реальный `DeadlockDetectedError`; captured logs зафиксировали attempts `1/3`
  и `2/3`, после третьего `40P01` вышел наружу.
- Target после P105 требует оба outer batch успешными и проверяет четыре `COMMITTED`, долги
  `3.00000000` на `A→B` и `B→C`, ноль `PrepareLock`, четыре payment audit rows и неизменные trust
  limits (`:284-345`). До
  P105 test помечен `xfail(strict=True)` (`:195-198`); canonical повтор с `-TaskSlug wave3_p102`
  — exit `0`, `1 xfailed`. Pinned `ruff==0.1.14` и `git diff --check` — exit `0`. Test commit:
  `a68aed09ec272cd4e03ea805790da45fb2f88c3f`.
- Независимый read-only review подтвердил реальный engine/savepoint reproducer, но нашёл будущий
  false-negative: coarse owner lock заблокирует второй batch ещё до завершения его первого call.
  Harness расширен PostgreSQL `pg_locks` barrier: он выпускает holder либо после двух current-path
  first calls, либо когда видит реальное ожидание нового advisory owner lock (`:212-234`). Добавлена
  countercheck неизменных trust limits и зафиксирован production retry count. Повторный unmarked
  RED `wave3_p102_red_reviewfix` — exit `1`, тот же единственный настоящий `40P01`; строгий final
  selector `wave3_p102_final` — exit `0`, `1 xfailed`; Ruff/diff — exit `0`. Review-remediation
  commit: `52273f0f86abe3a01c43bff39841bdbd70d040ed`.

### 2026-08-11 — P103

#### Current / Intended / Optimal

- **Current.** Повторный inventory командой
  `rg -n "PaymentEngine\(|commit=False|create_payment_internal_staged|execute_planned_payments" app`
  — exit `0`. Все production-конструкторы `PaymentEngine` исчерпываются сервисом
  (`app/core/payments/service.py:193-197`), Admin abort (`app/api/v1/admin.py:1014`), expired-lock
  recovery (`app/core/recovery.py:102`) и stale-payment recovery (`recovery.py:208`). Старый
  service anchor в спеке сдвинулся до `service.py:304-339`, но staged finding подтверждается:
  `commit=False` остаётся на `:328-334`. Единственный multi-call staged payment owner — real tick:
  один `action_db_lock`/session на `real_payments_executor.py:290-291,365-379`, задачи создаются на
  `:465`, outer session живёт в `real_tick_orchestrator.py:233-383`, durable commit принадлежит
  `real_tick_persistence.py:138-144`. Admin владеет одним staged abort плюс audit и commit/rollback
  (`admin.py:1015-1050`); оба recovery loops вызывают item-owned `abort(commit=True)`
  (`recovery.py:107-136,212-230`). Stop-level anchor drift нет.
- **Intended.** Каждый payment transition сохраняет явного владельца транзакции; `commit=False`
  не завершает outer transaction. Один outer owner не накапливает coarse locks в неизвестном
  порядке, а public service, Admin и recovery используют тот же lock domain до tx/pair locks.
- **Optimal bounded target.** Выбран equivalent-scoped coarse owner lock, а не глобальный payment
  mutex и не предварительное вычисление всех route pairs. Стабильный ключ имеет отдельный advisory
  namespace и identity одного `Equivalent`; все equivalent keys сортируются до acquisition. Это
  сериализует денежные transitions внутри эквивалента, где staged route set заранее неизвестен,
  но сохраняет параллелизм независимых эквивалентов. Global mutex был бы меньше по коду, однако
  без необходимости сериализовал бы весь протокол; complete pair set потребовал бы маршрутизировать
  весь batch до первой mutation и расширил бы owner refactor.

#### Выбранный ownership protocol для P105

1. `prepare`, `prepare_routes`, `commit` и `abort` получают полный набор equivalent-owner keys до
   tx и segment keys; для persisted flows `commit`/`abort` делают read → owner-lock → tx-lock →
   authoritative re-read/revalidation.
2. Real tick до создания action tasks извлекает полный набор эквивалентов из planned batch и
   pre-acquire-ит его в одном sorted call на общей outer session. Поэтому несколько savepoints не
   могут накопить equivalent locks в противоположном порядке.
3. Admin abort и recovery используют тот же engine boundary. Admin сохраняет один внешний commit
   audit+abort; recovery сохраняет item-owned commits. Пустой/non-monetary terminal path не обязан
   брать фиктивный equivalent lock и никогда не добирает его после tx lock.
4. Pair locks остаются нужны для узкой same-equivalent serialization/revalidation и получают
   канонический unordered identity в P104. Направление persisted flows, trustlines, audits и wire
   data не меняется.
5. P105 добавляет owner-surface regression через `RealPaymentsExecutor`; P102 остаётся focused
   engine/savepoint test. P106 отдельно фиксирует coordinated-quiescence или bridge rollout.

Независимый read-only inventory/review подтвердил owner map и equivalent-scoped coarse protocol как
наименьший механизм без глобальной потери межэквивалентного параллелизма. `git diff --check` по
`spec.md`, `plan.md`, `tasks.md` — exit `0`; product code в P103 не менялся. После задачи decision
record находится в `spec.md:348-394`, executable selection — в `plan.md:58-60`, P103 status — в
`tasks.md:49-50`.

### 2026-08-11 — P104

- Перед правкой повторно подтверждено: `app/core/payments/engine.py:82-93` хешировал
  `equivalent + from + to`, а unit contract требовал reverse inequality. Unit test сначала изменён
  на canonical equality с anti-vacuum counterchecks для другой пары и другого equivalent
  (`tests/unit/test_payment_engine_advisory_lock_key.py:8-36`). Canonical RED:
  `DEBUG=false; .\scripts\verify_local.ps1 -TaskSlug wave3_p104_unit_red -BackendOnly -BackendSelector tests/unit/test_payment_engine_advisory_lock_key.py`
  — exit `1`, `1 failed, 1 passed`, exact actual/target
  `assert 4826006079362130129 == 9181403266758904393`.
- Минимальная product-правка на `app/core/payments/engine.py:82-98` сортирует только UUID bytes двух
  участников перед SHA-256. `equivalent_id` остаётся частью key material; method arguments,
  persisted flows, trustline direction, audit payload и wire schema не менялись. Canonical unit
  повтор с `-TaskSlug wave3_p104_unit` — exit `0`, `2 passed`; counterchecks `other_pair` и
  `other_equivalent` остались неравны canonical key.
- Временные `xfail(strict=True)` сняты с P100/P101 (`test_payment_pair_advisory_locks_postgres.py:22`
  и `test_payment_inverse_multisegment_postgres.py:231`). Canonical PostgreSQL 16 milestone:
  `DEBUG=false; TEST_DATABASE_URL=postgresql+asyncpg://geo:geo@localhost:55433/geov0_test_wave3; GEO_TEST_ALLOW_DB_RESET=1; $selectors=@('tests/integration/test_payment_pair_advisory_locks_postgres.py','tests/integration/test_payment_inverse_multisegment_postgres.py','tests/integration/test_payment_commit_advisory_locks_postgres.py'); .\scripts\verify_local.ps1 -TaskSlug wave3_p104_pg -BackendOnly -BackendMarker postgres -BackendSelector $selectors`
  — exit `0`, `8 passed`. Оба start orders теперь реально ждут один pair lock и затем дают два
  `COMMITTED`, направленные net debts `1.00000000`, ноль PrepareLocks, две audit rows и неизменные
  limits (`test_payment_inverse_multisegment_postgres.py:300-384`).
- P102 после P104 намеренно остаётся открытым до P105: canonical
  `wave3_p104_staged_guard` на `test_payment_staged_multicall_postgres.py` — exit `0`, `1 xfailed`.
  Это доказывает, что pair canonicalization не подменяет transaction-wide owner fix. Pinned
  `ruff==0.1.14` и `git diff --check` — exit `0`. Implementation commit:
  `2b00246b8106e15ec8f2e73857847d77fdd2e739`.

### 2026-08-11 — P105

- Перед product-правкой unit characterization
  `DEBUG=false; .\scripts\verify_local.ps1 -TaskSlug wave3_p105_unit_red -BackendOnly -BackendSelector tests/unit/test_payment_engine_advisory_locks_execute.py,tests/unit/test_payment_engine_retry_savepoint_nocommit.py,tests/unit/test_real_payments_ordered_journal.py`
  завершился exit `1`, `2 failed, 29 passed`: отсутствовал equivalent-owner key, а executor дошёл
  до трёх staged calls без batch pre-acquire. Это был RED именно нового owner boundary, не
  синтетический DBAPI schedule.
- Реализация вводит отдельный equivalent-owner namespace и стабильный signed key, сортирует и
  дедуплицирует полный набор до tx/pair locks (`app/core/payments/engine.py:47,116-150`).
  `prepare`/`prepare_routes` берут owner до tx (`:510-511,732-733`); `commit`/`abort` делают
  persisted-flow preflight → owner → tx → authoritative comparison (`:305-330,966-1037,
  1615-1681`). Изменившийся между preflight и tx набор не приводит к позднему lock acquisition:
  engine-owned UoW целиком откатывается и повторяется, staged UoW получает существующий публичный
  retryable `409/E008` (`:401-424`). Локальный savepoint retry для staged `40P01/40001` запрещён
  (`:427-431`), configured retry count не увеличен.
- Service boundary резолвит коды в UUID и сохраняет `55P03` как bounded timeout
  (`app/core/payments/service.py:341-381`). Real tick после initialization boundary открывает
  monetary UoW полным configured owner set (`app/core/simulator/real_tick_orchestrator.py:275-283`),
  а executor перед action tasks повторно фиксирует точный sorted planned set
  (`app/core/simulator/real_payments_executor.py:290-300`). Unit owner-surface regression доказывает,
  что этот вызов первый и единственный перед staged calls
  (`tests/unit/test_real_payments_ordered_journal.py:184-234`). Это уточняет P103: executor planned
  set сохранён, а tick-level configured set добавлен до более ранних monetary phases; история P103
  выше не переписывалась.
- Реальный P102 после coarse owner подтвердил ожидаемую границу snapshot: промежуточный unmarked
  `wave3_p105_p102_unmarked` завершился exit `1` с повторным `40001` внутри прежнего outer
  `SERIALIZABLE` snapshot. Тест исправлен на один fail-fast outer rollback и повтор всего batch в
  свежей транзакции; `wave3_p105_p102_restart` — exit `0`, `1 passed`. Следующий combined прогон
  `wave3_p105_core_pg` честно завершился exit `1`, `1 failed, 3 passed`, потому что невидимый
  конкурентный insert Debt дал независимый `23505` (предмет P107); fixture заменён существующими
  Debt rows без изменения production retry policy.
- Старые конкурентные tests сначала честно дали `wave3_p105_existing_pg` — exit `1`, `4 failed,
  3 passed`: три barrier ожидали уже недостижимые segment/tx hooks, один abort обнаружил изменение
  owner preflight. Barriers перенесены на equivalent-owner boundary с прежними monetary/state/lock
  counterchecks; engine-owned abort теперь повторяет полный порядок после rollback. Повтор
  `wave3_p105_existing_pg3` — exit `0`, `7 passed`. Дополнительный real-PG anti-vacuum test
  (`tests/integration/test_payment_staged_multicall_postgres.py:363-438`) доказывает, что обратные
  multi-equivalent input orders ждут один sorted set, а независимый equivalent проходит параллельно.
- Прямой SQL lookup в раннем WIP оркестратора дал canonical
  `wave3_p105_tick_units` — exit `1`, `3 failed, 1 passed`, exact error
  `AttributeError: '_SuccessfulRollbackSession' object has no attribute 'execute'`. Lookup перенесён
  в общий `PaymentService`; `wave3_p105_tick_units2` — exit `0`, `4 passed`. Финальный unit command
  с engine/service-executor/tick selectors, `-TaskSlug wave3_p105_unit_final`, — exit `0`,
  `52 passed`.
- Проверенный disposable PostgreSQL 16 endpoint: database `geov0_test_wave3`, user `geo`, port
  `55433`; guard подтвердил отдельную DB. Финальный post-commit milestone:
  `DEBUG=false; TEST_DATABASE_URL=postgresql+asyncpg://geo:geo@localhost:55433/geov0_test_wave3; GEO_TEST_ALLOW_DB_RESET=1; $selectors=@('tests/integration/test_payment_pair_advisory_locks_postgres.py','tests/integration/test_payment_inverse_multisegment_postgres.py','tests/integration/test_payment_staged_multicall_postgres.py','tests/integration/test_payment_commit_advisory_locks_postgres.py','tests/integration/test_concurrent_prepare_routes_bottleneck_postgres.py'); .\scripts\verify_local.ps1 -TaskSlug wave3_p105_pg_final -BackendOnly -BackendMarker postgres -BackendSelector $selectors`
  — exit `0`, `12 passed`.
- `python -m ruff --version` подтвердил pinned `ruff 0.1.14`; pinned Ruff по 12 изменённым source/test
  paths и `git diff --check` — exit `0`. Implementation commit:
  `4ca910c77f29509e6d0e89a798563d92aba29bb6`; task/evidence находятся в
  `tasks.md:53-54` и этой append-only записи.

### 2026-08-11 — P106

#### Current / Intended / Optimal

- **Current.** После P105 все одноверсионные owners входят в новый protocol через один engine:
  public/internal service создаёт `PaymentEngine` на `app/core/payments/service.py:196`; staged tick
  pre-acquire расположен на `real_tick_orchestrator.py:275-283` и
  `real_payments_executor.py:290-300`; Admin abort использует `PaymentEngine.abort(commit=False)` на
  `app/api/v1/admin.py:1014,1029-1034`; expired-lock и stale-payment recovery используют тот же
  `abort(commit=True)` на `app/core/recovery.py:102-122,208-226`. Legacy процессы знают только
  directed pair keys, а новый код — equivalent-owner плюс canonical unordered pair keys; общего
  persisted version marker или runtime negotiation нет.
- **Intended.** Mixed-version workers не должны одновременно изменять payments: old reverse worker
  может обойти canonical pair identity, а old staged owner не участвует в equivalent-owner domain.
  Нельзя называть обычный rolling deploy совместимым без bridge, описанного в §9.
- **Optimal bounded target.** Выбран **coordinated quiescence**, а не compatibility bridge. В
  репозитории нет zero-downtime deployment requirement; bridge добавил бы canonical и оба legacy
  directional keys каждому pair во всех service/staged/Admin/recovery paths и заметно расширил бы
  механизм P105. Поэтому upgrade и rollback Phase 1/Phase 2 выполняются только так: остановить все
  payment writers и real ticks/recovery, дождаться завершения или отката их DB transactions и
  отсутствия advisory-lock holders, развернуть одну версию на всех owner surfaces, затем возобновить
  writers. Rollback требует той же quiescence. Совместная работа old/new версий **не поддерживается
  и не тестировалась**.

Одноверсионная owner-матрица проверена canonical командой
`DEBUG=false; $selectors=@('tests/unit/test_payments_2pc.py','tests/unit/test_real_payments_ordered_journal.py','tests/unit/test_admin_abort_tx.py','tests/unit/test_recovery_cleanup.py','tests/unit/test_payment_engine_advisory_locks_execute.py'); .\scripts\verify_local.ps1 -TaskSlug wave3_p106_owner_matrix -BackendOnly -BackendSelector $selectors`
— exit `0`, `58 passed`. Фактический cache artifact содержит 58 nodeids, включая 9 Admin-abort,
6 recovery, executor pre-acquire и параметризованную матрицу всех четырёх engine transitions
`owner → tx → first tx read`; это не пустой selector. `rg -n "PaymentEngine\\(|await engine\\.abort|create_payment_internal_staged|acquire_staged_equivalent_owner_locks"` по пяти owner files — exit `0` и
подтвердил перечисленные anchors. Product code в P106 не менялся; stable RU deployment wording
переносится без изменения решения в P108. `git diff --check` для `spec.md`/`tasks.md` — exit `0`.

### 2026-08-11 — P105 review remediation

- Независимый read-only audit exact HEAD
  `f6f082a43ded38586b84b3061ca1e58d4f8a953e` подтвердил lock order, staged outer restart,
  executor/tick coverage и monetary counterchecks, но нашёл два P2. Первый — опечатка в evidence
  выше: SHA `4ca910c77f29509e6d0e89a798563d92aba29bb6` не существует; правильный implementation commit —
  `4ca910c42baf10c865339663e43c2244a43d7242`. Историческая строка сохранена, это append-only
  correction.
- Второй P2: Admin owner вызывал `abort(commit=False)` без внешней deadline, тогда как staged
  `_run_uow_with_retry` намеренно не устанавливает `SET LOCAL lock_timeout`. Теперь Admin оборачивает
  весь staged abort в bounded `asyncio.wait_for`, использует тот же минимум payment-total/commit
  budget и при истечении возвращает существующий `504/E007`; общий `BaseException` rollback
  по-прежнему атомарно откатывает audit+abort (`app/api/v1/admin.py:1031-1059`). Regression
  `tests/unit/test_admin_abort_tx.py:180-239` удерживает abort бесконечно, наблюдает cancellation,
  `504/E007`, исходный `WAITING` и отсутствие durable audit.
- Canonical remediation gate:
  `DEBUG=false; $selectors=@('tests/unit/test_admin_abort_tx.py','tests/contract/test_openapi_contract.py'); .\scripts\verify_local.ps1 -TaskSlug wave3_p105_admin_timeout_final -BackendOnly -BackendSelector $selectors`
  — exit `0`, `33 passed`; pinned Ruff и `git diff --check` по Admin source/test — exit `0`.
  Отдельное предположение audit о late terminal pair lock после tx было отозвано самим reviewer:
  на поддерживаемых одноверсионных paths matching equivalent owner уже удерживается, новый owner
  после tx не приобретается.

### 2026-08-11 — P107 STOP: real SQLSTATE расходится с записанным target

#### Current / Intended / Optimal

- **Current.** Predicate всё ещё считает любой `23505` retryable только для `op="commit"`
  (`app/core/payments/engine.py:333-353`), а unit test проверяет эту таблицу синтетическим exception
  (`tests/unit/test_payment_engine_retry_savepoint_nocommit.py:91-103`). Но два независимых real
  PostgreSQL 16 schedule после P105 не подтвердили записанный SQLSTATE. Same-`tx_id` waiter и
  two-`tx_id` waiter оба начали `SERIALIZABLE` snapshot до equivalent-owner wait; holder затем
  durable-вставил отсутствующий Debt. В обоих случаях настоящий waiter
  `INSERT INTO debts (...) RETURNING ...` получил `40001`, не `23505`.
- **Intended.** P107 и §4.4 требуют deterministic real `23505` после `SERIALIZABLE` advisory wait и
  whole-commit retry. Predicate/unit не могут заменить это runtime evidence. Same-`tx_id` target
  дополнительно должен разрешиться через durable `COMMITTED` без второго money/audit effect.
- **Optimal.** Для фактического production path PostgreSQL 16 естественный target — оставить
  доказанный whole-UoW `40001` retry и либо удалить/сузить недоказанный broad commit-`23505`
  predicate, либо предоставить другой реальный production schedule, в котором именно commit-owned
  UoW действительно получает `23505`. Подмена на `READ COMMITTED`, искусственный UUID collision или
  synthetic DBAPI не удовлетворяет текущей спеке. Эти варианты меняют failure contract по-разному,
  поэтому требуется решение владельца до изменения кода или P107 target.

Evidence сохранено без переписывания провалов:

- `wave3_p107_23505_target` (same `tx_id`) — exit `1`, exact actual/target
  `assert '40001' == '23505'`; holder/waiter schedule использовал real ungranted advisory row из
  `pg_locks`.
- `wave3_p107_23505_twotx2` — exit `1`, exact actual/target
  `assert ['40001'] == ['23505']`.
- debug-only повтор двух `tx_id` записал actual tuple
  `('40001', 'INSERT INTO debts (...) RETURNING debts.created_at, debts.updated_at')`; exit `1`.

P107 остаётся `[!]`; экспериментальные test changes не закоммичены, product predicate не менялся.

### 2026-08-11 — P107 resolution

- Владелец разрешил скорректировать target по runtime evidence и удалить либо сузить broad
  commit-`23505` predicate. Первая реализация полностью удалила `23505`; unit
  `wave3_p107_unit` — exit `0`, `14 passed`. Однако следующий combined real-PG run
  `wave3_p107_real40001` честно завершился exit `1`, `1 failed, 1 passed`: тот же two-`tx_id`
  schedule на этот раз получил настоящий
  `UniqueViolationError ... uq_debts_debtor_creditor_equivalent`, то есть допустимый серверный
  исход действительно меняется между `40001` и `23505`. Полное удаление отменено до commit.
- Финальный classifier сохраняет whole-UoW retry для `40P01/40001`, а `23505` принимает только при
  одновременном совпадении `op="commit"`, `INSERT INTO debts` и constraint
  `uq_debts_debtor_creditor_equivalent`; extractor поддерживает asyncpg cause chain и psycopg
  `diag.constraint_name` (`app/core/payments/engine.py:333-382`). Unit anti-vacuum
  `tests/unit/test_payment_engine_retry_savepoint_nocommit.py:83-127` доказывает положительный
  Debt case и отрицательные staged/prepare/abort, другой constraint и другую таблицу.
- Два real schedules используют явный `SERIALIZABLE`, owner-attempt events и фактическую
  ungranted advisory row из `pg_locks`, ошибки не инъецируют. Same-`tx_id` regression
  (`tests/integration/test_payment_commit_advisory_locks_postgres.py:342-505`) требует один rollback,
  два preflight, один Debt `8`, отсутствие reciprocal Debt/PrepareLocks, один PAYMENT audit,
  неизменный trust limit и terminal `COMMITTED` без error. Two-`tx_id` regression
  (`tests/integration/test_payment_engine_uow_retry_postgres.py:196-438`) требует один реальный
  `INSERT INTO debts` conflict из `{40001,23505}`, один whole-UoW retry, два `COMMITTED`, Debt `6`,
  два audits, ноль locks и неизменный limit.
- После корректировки narrow assertion первый полный PG milestone
  `wave3_p107_pg` завершился exit `1`, `1 failed, 9 passed`: same-`tx_id` получил `23505` вместо
  излишне точного ожидания `40001`, при этом product retry уже прошёл. Это дополнительное runtime
  evidence двух допустимых SQLSTATE; ожидание исправлено на bounded set без ослабления финальных
  инвариантов.
- Финальный PostgreSQL 16 milestone:
  `DEBUG=false; TEST_DATABASE_URL=postgresql+asyncpg://geo:geo@localhost:55433/geov0_test_wave3; GEO_TEST_ALLOW_DB_RESET=1; $selectors=@('tests/integration/test_payment_commit_advisory_locks_postgres.py','tests/integration/test_concurrent_prepare_routes_bottleneck_postgres.py','tests/integration/test_payment_idempotency_postgres.py','tests/integration/test_payment_engine_uow_retry_postgres.py'); .\scripts\verify_local.ps1 -TaskSlug wave3_p107_pg_final -BackendOnly -BackendMarker postgres -BackendSelector $selectors`
  — exit `0`, `10 passed`. Cache artifact просмотрен: ровно 10 nodeids, включая оба real conflict
  schedules, весь same-tx/commit-abort файл, два bottleneck paths и concurrent idempotency.
- Отдельный timeout/cancellation/recovery gate:
  `DEBUG=false; $selectors=@('tests/unit/test_payment_engine_retry_savepoint_nocommit.py','tests/unit/test_payment_timeouts.py','tests/unit/test_payment_cleanup_cancellation.py','tests/unit/test_recovery_cleanup.py','tests/integration/test_payment_prepare_error_taxonomy.py::test_prepare_cancellation_preserves_cancel_and_durably_aborts','tests/integration/test_payment_prepare_error_taxonomy.py::test_cancellation_at_other_payment_phases_has_terminal_state','tests/integration/test_payment_prepare_error_taxonomy.py::test_staged_prepare_cancellation_aborts_before_outer_rollback','tests/integration/test_payment_prepare_error_taxonomy.py::test_repeated_cancellation_during_recovery_read_still_aborts'); .\scripts\verify_local.ps1 -TaskSlug wave3_p107_siblings -BackendOnly -BackendSelector $selectors`
  — exit `0`, `22 passed`; cache artifact содержит все 22 ожидаемых nodeids. Pinned Ruff
  `0.1.14` по четырём изменённым code/test files и `git diff --check` — exit `0`.
  Implementation commit: `1a9cc63e54df1f0b493fdcb1773a501396e84cbc`; P107 task закрыт в
  `tasks.md:58-61`. Предыдущая STOP-запись и все неудачные попытки выше сохранены.

### 2026-08-11 — P108

- До изменения stable decision surface описывал только общий Defence in Depth и payment error
  mapping (`docs/ru/09-decisions-and-defaults.md:212-225,273-289`), а simulator payment owner —
  Redis sender/equivalent lock, timeout и `40001/40P01`
  (`docs/ru/simulator/backend/payment-integration.md:29-35,68-70,146-149,179-185,222-223`). В этих
  анкорах отсутствовали canonical unordered pair identity, equivalent-owner order, mixed-version
  rollout, узкий Debt-`23505` и явный статус clearing interlock.
- После изменения decision surface фиксирует lock-only canonical pair, порядок полного owner set →
  tx → pair, outer-UoW retry, обязательную coordinated quiescence и открытый Phase 2 interlock
  (`docs/ru/09-decisions-and-defaults.md:226-246`), а также server-selected `40001`/узкий exact
  Debt-`23505` без утечки SQL/constraint наружу (`:303-308`). Simulator payment owner синхронизирован
  в `docs/ru/simulator/backend/payment-integration.md:29-38,73-77,155-166,197-205,241-246`.
- Проверка обязательных фраз и всех относительных Markdown links в двух изменённых документах —
  `P108_DOC_CONTRACT_OK`, exit `0`; `git diff --check -- docs/ru/09-decisions-and-defaults.md docs/ru/simulator/backend/payment-integration.md`
  — exit `0`. Документальный implementation commit:
  `fcb8cf7f121bc45f2fb1fcf57e3cae204ba14f70`. Независимый пользовательский metadata-hunk в начале
  `docs/ru/09-decisions-and-defaults.md` сохранён в рабочем дереве и не попал в commit.

### 2026-08-11 — P109 review: staged `lock_timeout` remediation

- **Current на frozen `303e17572a5dad27a7a938addd4650b671c5cdee`.** Публичный staged helper
  вызывал owner acquisition напрямую (`app/core/payments/engine.py:145-150`), а тот выполнял
  `SET LOCAL lock_timeout` (`:203-215`) без восстановления. Два независимых read-only PostgreSQL 16
  probe получили `before=0; after=5s`; при budget `100ms` последующее независимое advisory wait
  завершилось `55P03` через `0.094s`. Targeted unit gate при этом был false-green: canonical
  `wave3_p109_internal_review` — exit `0`, `48 passed`.
- **Intended / Optimal.** Payment deadline ограничивает только acquisition; staged caller владеет
  остальной внешней транзакцией. После полного успеха helper должен точно восстановить прежний
  transaction-local timeout; после `55P03`/cancellation он не маскирует исходную ошибку и оставляет
  rollback outer owner.
- Remediation `7d8d1e2c756cb9485a4062beffefc499ced05680` сохраняет `SHOW lock_timeout`, захватывает полный
  owner set и восстанавливает значение через transaction-local `set_config`
  (`app/core/payments/engine.py:145-170`). PG anti-vacuum test
  (`tests/integration/test_payment_staged_multicall_postgres.py:441-487`) проверяет `0` до/после и
  доказывает, что независимое ожидание остаётся активным через `200ms` при payment budget `50ms`.
  Canonical `wave3_p109_timeout_restore` — exit `0`, `1 passed`; unit owner matrix
  `wave3_p109_timeout_restore_unit` — exit `0`, `65 passed`; полный post-remediation PG matrix
  `wave3_p109_pg_remediation` — exit `0`, `16 passed`.
- Internal reviewer exact `7d8d1e2` — CLEAN: отдельный PG probe сохранил custom `750ms`, а downstream
  wait получил timeout через `0.750s`, не `0.050s`. External Codex `gpt-5.6-sol` remediation review
  также CLEAN по этому P2: isolated disposable DB сохранила custom `73ms`; `git diff --check
  303e175..7d8d1e2` — exit `0`. Новых P1/P2 в fix delta не найдено.
- Неблокирующая append-only correction: прежняя P102 evidence-строка `spec.md:338-340` называла
  debts `3.00000000`; фактический regression на текущем target требует `4.00000000`
  (`tests/integration/test_payment_staged_multicall_postgres.py:321-330`). Исходная историческая
  строка сохранена.

### 2026-08-11 — P108/P109 STOP: runtime и stable docs выбрали разные tick semantics

#### Current / Intended / Optimal

- **Current behavior.** Staged retryable conflict пробрасывается из executor, payments coordinator
  вызывается один раз (`app/core/simulator/real_tick_orchestrator.py:295-313`), затем tick делает
  rollback и записывает `REAL_MODE_TICK_FAILED` (`:502-572`). Regression
  `tests/unit/test_real_tick_orchestrator_rollback_resolution.py:240-267` явно требует
  `coordinator.calls == 1`. Следующий heartbeat сначала увеличивает `tick_index`
  (`app/core/simulator/runtime_impl.py:916-933`) и планирует новую работу; это не replay того же batch.
- **Intended behavior.** Спека §4.4 разрешает два target: детерминированно вернуть конфликт outer
  owner **или** повторить весь outer UoW. Реализация и старый regression выбрали первый, но P108
  stable docs теперь обещают второй: повтор batch на новой session
  (`docs/ru/09-decisions-and-defaults.md:231-235`,
  `docs/ru/simulator/backend/payment-integration.md:158-162`). Canonical selector
  `wave3_ext_review_outer_retry` — exit `0`, `1 passed`, тем самым подтверждает именно fail-fast.
- **Optimal target требует решения владельца.** Вариант A сохраняет проверенный fail-fast: откатить
  tick, не переигрывать тот же batch, исправить обе stable docs и P108 evidence. Вариант B добавляет
  bounded retry выше создания session и повторяет тот же детерминированный batch с сохранёнными
  idempotency/observation semantics. B меняет availability, error accounting и tick semantics и
  требует отдельного кода и end-to-end schedules; его нельзя вывести как техническую поправку.

P108 снова `[!]`, P109 не закрывается до выбора владельца и повторного exact-head external review.

### 2026-08-11 — P108 owner resolution

- Владелец выбрал вариант A: сохранить реализованный fail-fast rollback. До решения stable docs
  обещали повтор той же единицы работы/batch на новой session
  (`docs/ru/09-decisions-and-defaults.md:234-235`,
  `docs/ru/simulator/backend/payment-integration.md:160-162` до commit ниже), что противоречило
  runtime regression.
- После решения decision owner разделяет engine-owned whole-UoW retry и staged real-tick fail-fast:
  tick откатывается, получает `REAL_MODE_TICK_FAILED`, тот же batch не переигрывается, следующий
  heartbeat планирует новый tick (`docs/ru/09-decisions-and-defaults.md:234-237`). Simulator payment
  owner фиксирует тот же контракт в `docs/ru/simulator/backend/payment-integration.md:160-164,203-207`.
- Canonical runtime selector:
  `DEBUG=false; .\scripts\verify_local.ps1 -TaskSlug wave3_p108_failfast_contract -BackendOnly -BackendSelector tests/unit/test_real_tick_orchestrator_rollback_resolution.py::test_retryable_payment_conflict_rolls_back_tick_transaction`
  — exit `0`, `1 passed`. Проверка четырёх обязательных contract-фраз —
  `P108_FAILFAST_DOCS_OK`, exit `0`; `git diff --check` для двух stable docs — exit `0`.
  Normative docs commit: `d1937e4221e88634ddeeb9eeb74cb25a05593a1c`. Пользовательский metadata-hunk
  в начале decision owner снова исключён из commit и сохранён в рабочем дереве. P108 закрыта;
  требуется финальный exact-head review P109.

### 2026-08-11 — P109 final exact-head review

- Review lifecycle сохранён полностью: первый Option A docs commit `d1937e4` исправил основной
  раздел, но оставил вторую положительную фразу «мог переиграть»; внешний reviewer нашёл её на
  `4552d42737108994e9ef7aae2ba8b24a88aa0168`. Узкий docs-only commit
  `6227fc58a58bc69efbe4fc3d97da28b661b36d66` заменил дубликат на rollback,
  `REAL_MODE_TICK_FAILED`, отсутствие same-batch replay и новый tick следующего heartbeat.
  `P108_DECISION_DUPLICATE_OK` и `git diff --check` — exit `0`.
- Обязательный внешний reviewer — авторизованный владельцем Codex `gpt-5.6-sol` (resolved runtime
  model ID интерфейс не раскрывает). Он проверил exact range
  `39f960e0f2a5581374396871c1018b040c990036..6227fc58a58bc69efbe4fc3d97da28b661b36d66`
  в standalone clone `E:\Temp\geov0-wave3-final-review-73a2b30722114d5686b3c61df4eaf60f`:
  отдельный `.git`, remote отсутствует, local credential helper пуст, tracked status clean; shared
  tree не менялся. Вердикт **CLEAN**, открытых P1/P2 нет.
- External canonical checks на exact `6227fc5`: fail-fast selector
  `scripts/verify_local.ps1 -TaskSlug wave3_ext_final_failfast_6227 -BackendOnly -BackendSelector tests/unit/test_real_tick_orchestrator_rollback_resolution.py::test_retryable_payment_conflict_rolls_back_tick_transaction`
  — exit `0`, `1 passed`; lock-timeout PG selector
  `scripts/verify_local.ps1 -TaskSlug wave3_ext_final_timeout_6227 -BackendOnly -BackendMarker postgres -BackendSelector tests/integration/test_payment_staged_multicall_postgres.py::test_staged_owner_restores_outer_transaction_lock_timeout_postgres`
  — exit `0`, `1 passed`; targeted unit/Admin/recovery/cancellation matrix с `-TaskSlug
  wave3_ext_final_unit_matrix_6227` — exit `0`, `71 passed` и 71 cache nodeids; семь PG owner
  selectors с `-TaskSlug wave3_ext_final_pg_matrix_6227 -BackendMarker postgres` — exit `0`,
  `16 passed` и 16 nodeids. Stable-doc
  forbidden-positive-replay check и full-range `git diff --check` — exit `0`. Его disposable DB
  `geov0_test_wave3_finalrev_73a2b307` создана после absence check и удалена после active=`0`.
- Финальный локальный canonical milestone после product remediation:
  `DEBUG=false; .\scripts\verify_local.ps1 -TaskSlug wave3_phase1_final_exact` — exit `0`; backend
  `978 passed, 3 skipped, 23 deselected`, один Alembic head; Admin UI lint/test/build и Simulator UI
  lint/typecheck/unit/build прошли, Simulator unit — `729 passed`. Прогон начался на `4552d42`; до
  reviewed `6227fc5` менялись только normative Markdown, product/test tree идентичен. Exact-head docs
  и targeted runtime/PG checks выполнены внешним reviewer выше.

P109 закрыта; Phase 1 не имеет открытых P1/P2 и готова к последовательной Волне 4.

## Changelog

### 2026-08-11 — Волна 3 / Program 002 Phase 1 закрыта

- P100–P109 завершены: reverse pair contention, inverse/staged real-PG schedules, canonical unordered
  pair key, equivalent-owner protocol, quiescent rollout, узкий Debt-`23505`, stable RU docs и
  exact-head review.
- Adversarial review выявил и закрыл staged `lock_timeout` leak; владелец выбрал fail-fast tick
  semantics без same-batch replay. Финальный внешний вердикт на `6227fc5` — CLEAN.
- Phase 2 остаётся авторизованной частью Волны 4 и выполняется только после программы 003.
- Remote branch после closeout указывала на `b0f10d61f59f14c46bbc9b4487ca120aff9d78e7` (локальный SHA и
  `git ls-remote` совпали). Manual Quality run `31513434549` на этом exact SHA завершился success:
  required local-equivalent gates, active UI Chromium smoke, blocking Ruff diagnostics, PostgreSQL
  integration, development-image content policy, production-like container/schema smoke, Admin E2E,
  Simulator visual E2E и super-smoke — каждый job success. Единственная annotation — предупреждение
  GitHub о принудительном Node 24 для action runtime; product gate не падал.

## 12. Phase 2 implementation evidence (append-only)

### 2026-08-11 — P200

- Перед test-правкой finding повторно подтверждён на `a5c0f64`: после программы 003 clearing
  завершает skip/commit корректно, но shared payment lock всё ещё не берёт. Current order:
  replay read → `Debt FOR UPDATE` (`app/core/clearing/service.py:962-1000`) → единственный
  PrepareLock snapshot (`:1030-1052`) → mutation/commit (`:1182-1257`). Payment prepare делает
  equivalent-owner → tx → canonical pair (`app/core/payments/engine.py:556-618`) до записи
  PrepareLock/state (`:620-739`). Старые anchors §2.4 сдвинулись, но находка полностью
  подтверждается; stop-level drift нет.
- Добавлен реальный PostgreSQL schedule commit `d2ea68f`, без synthetic DBAPI errors:
  `tests/integration/test_clearing_payment_prepare_interlock_postgres.py:204-314` держит clearing
  после фактического пустого snapshot и запускает reverse prepare; `:344-452` держит
  `commit=False` reverse prepare невидимой и запускает clearing. Оба используют независимые
  SERIALIZABLE sessions, server-side advisory-wait observation, bounded tasks и проверяют terminal
  state/locks/debt versions.
- Canonical unmarked RED:
  `$env:DEBUG='false'; $env:ENV='test'; $env:TEST_DATABASE_URL='postgresql+asyncpg://geo:geo@127.0.0.1:55433/geov0_test_wave4_p200_a5c0'; $env:GEO_TEST_ALLOW_DB_RESET='1'; .\scripts\verify_local.ps1 -TaskSlug wave4_002_p200_red -BackendOnly -BackendMarker postgres -BackendSelector tests/integration/test_clearing_payment_prepare_interlock_postgres.py`
  — exit `1`, `2 failed`. Exact actual/target: `reverse prepare crossed clearing's empty conflict
  decision` и `clearing bypassed the uncommitted reverse prepare owner`. Это два порядка одной
  подтверждённой shared-boundary причины, а не timeout harness.
- До P201 оба теста помечены `xfail(strict=True)`. Та же canonical команда с `-TaskSlug
  wave4_002_p200_characterization` — exit `0`, `2 xfailed`; cache содержит ровно два ожидаемых
  nodeid. Pinned `ruff==0.1.14` и `git diff --check` — exit `0`.

### 2026-08-11 — P201

- Перед implementation повторно разделены свидетельства по AGENTS.md §1. **Current:** clearing
  читал `Debt FOR UPDATE`, затем `PrepareLock`, не участвуя в payment owner domain; P200 дал два
  реальных forbidden schedule. **Intended:** новый prepare не может появиться между clearing
  decision и mutation (`§4.3`, `§8`). **Optimal:** отдельная lock-only транзакция берёт уже
  существующий Phase-1 equivalent-owner lock, после ожидания рабочая сессия откатывает прежний
  snapshot и делает authoritative replay/read заново; lock-транзакция живёт до terminal
  commit/rollback clearing. Это минимальный общий boundary без schema/API/migration и без изменения
  направленной денежной семантики. Work-session `pg_try_advisory_xact_lock` был отвергнут до commit:
  сам SELECT мог зафиксировать SERIALIZABLE snapshot до освобождения lock и пропустить новый
  `PrepareLock`.
- Реализация: cancellation-safe release lock-only session
  `app/core/clearing/service.py:70-92`; PostgreSQL preflight, отдельный `AsyncEngine` bind,
  `PaymentEngine.acquire_staged_equivalent_owner_locks`, post-wait rollback/fresh execution и
  `55P03`→существующий timeout contract — `:959-1039`; authoritative equivalent revalidation после
  `Debt FOR UPDATE` — `:1107-1145`. Порядок остаётся payment/clearing owner → рабочие Debt rows;
  clearing не держит Debt row во время ожидания owner lock.
- Старые Program-003 replay/skip tests адаптированы к новому boundary, не ослаблены:
  `tests/integration/test_clearing_commit_replay_postgres.py:17-232` доказывает один durable effect
  и replay двух SERIALIZABLE callers через реальный granted/ungranted advisory join; `:293-492`
  фиксирует настоящий `40001` после свежего post-lock snapshot и доказывает, что без matching
  clearing transaction он остаётся `E010`. Недостижимый в валидной PostgreSQL схеме defensive
  `amount <= 0` больше не имитируется грязным ORM state через service-owned rollback; его локальная
  anti-vacuum проверка сохранена в `tests/unit/test_clearing_additional_cases.py:714-735`.
- Canonical real-PG core matrix на изолированной БД:
  `$env:DEBUG='false'; $env:ENV='test'; $env:TEST_DATABASE_URL='postgresql+asyncpg://geo:geo@127.0.0.1:55433/geov0_test_wave4_p200_a5c0'; $env:GEO_TEST_ALLOW_DB_RESET='1'; $selectors=@('tests/integration/test_clearing_payment_prepare_interlock_postgres.py','tests/integration/test_concurrent_clearing_payment_lost_update_postgres.py','tests/integration/test_clearing_skip_releases_locks_postgres.py','tests/integration/test_clearing_commit_replay_postgres.py'); .\scripts\verify_local.ps1 -TaskSlug wave4_002_p201_core4b -BackendOnly -BackendMarker postgres -BackendSelector $selectors`
  — exit `0`, `13 passed`. SQLite clearing unit matrix с `-TaskSlug wave4_002_p201_units` — exit
  `0`, `37 passed`; отдельный defensive nonpositive selector с `-TaskSlug
  wave4_002_p201_nonpositive_unit` — exit `0`, `1 passed`.

### 2026-08-11 — P202

- Оба порядка P200 теперь проверяют полный boundary outcome. Clearing-first в
  `tests/integration/test_clearing_payment_prepare_interlock_postgres.py:304-375` фиксирует
  фактический locked amount `30`, один `COMMITTED` clearing с тем же `payload.amount` и исходным
  множеством Debt UUID, один verified CLEARING audit и ноль PAYMENT audit, ровно один reverse
  `PrepareLock`, оставшиеся Debts `70/version 2` и `10/version 2`, четыре неизменных trust limits.
  Payment-first в `:458-528` фиксирует `None`, durable `PREPARED`+один lock, отсутствие clearing
  transaction и любых boundary audits, все три исходных Debt amounts/versions и четыре неизменных
  limits.
- Сквозной payment-after-clearing schedule сохранён отдельно и усилен:
  `tests/integration/test_concurrent_clearing_payment_lost_update_postgres.py:226-299` проверяет
  actual clearing amount/payload `30`, итоговые Debts `120/version 3` и `10/version 2`, неизменные
  три limits, ноль PrepareLocks, ровно по одному verified PAYMENT/CLEARING audit и ровно одну
  `payment.received` publication для terminal payment.
- Canonical команда:
  `$env:DEBUG='false'; $env:ENV='test'; $env:TEST_DATABASE_URL='postgresql+asyncpg://geo:geo@127.0.0.1:55433/geov0_test_wave4_p200_a5c0'; $env:GEO_TEST_ALLOW_DB_RESET='1'; $selectors=@('tests/integration/test_clearing_payment_prepare_interlock_postgres.py','tests/integration/test_concurrent_clearing_payment_lost_update_postgres.py'); .\scripts\verify_local.ps1 -TaskSlug wave4_002_p202_effects -BackendOnly -BackendMarker postgres -BackendSelector $selectors`
  — exit `0`, `3 passed`.

### 2026-08-11 — P203

- Exact product/test HEAD `0ceaab76df5d3caf19a536b94bda18c9eff0b433` проверен общей payment/clearing
  PostgreSQL матрицей на заранее проверенной disposable DB
  `geov0_test_wave4_p200_a5c0`. В selector вошли семь Phase-1 payment owner файлов, Wave-2 audit
  conflict, четыре clearing ownership/interlock файла:
  `$env:DEBUG='false'; $env:ENV='test'; $env:TEST_DATABASE_URL='postgresql+asyncpg://geo:geo@127.0.0.1:55433/geov0_test_wave4_p200_a5c0'; $env:GEO_TEST_ALLOW_DB_RESET='1'; $selectors=@('tests/integration/test_payment_pair_advisory_locks_postgres.py','tests/integration/test_payment_inverse_multisegment_postgres.py','tests/integration/test_payment_staged_multicall_postgres.py','tests/integration/test_payment_commit_advisory_locks_postgres.py','tests/integration/test_concurrent_prepare_routes_bottleneck_postgres.py','tests/integration/test_payment_idempotency_postgres.py','tests/integration/test_payment_engine_uow_retry_postgres.py','tests/integration/test_payment_engine_audit_conflict_postgres.py','tests/integration/test_clearing_payment_prepare_interlock_postgres.py','tests/integration/test_concurrent_clearing_payment_lost_update_postgres.py','tests/integration/test_clearing_skip_releases_locks_postgres.py','tests/integration/test_clearing_commit_replay_postgres.py'); .\scripts\verify_local.ps1 -TaskSlug wave4_002_p203_pg_matrix -BackendOnly -BackendMarker postgres -BackendSelector $selectors`
  — exit `0`, `30 passed`.
- Фактический pytest cache открыт, а не принят по summary: ровно 30 nodeids, включая оба P200 order,
  unmatched-`40001`, acknowledgement-loss/cancellation replay, все skip branches, reverse pair,
  inverse multisegment, staged multi-owner/timeout restoration, same-tx/abort/prepare contention,
  idempotency, retry-UoW и audit-conflict. Отсутствующих измерений и marker-deselection нет.
