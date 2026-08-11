# 002 — Payment integrity follow-up

Status: Phase 0 complete; Phase 1 IN PROGRESS (owner-authorized 2026-08-11)

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
- Whole-UoW retry handles `40P01` and `40001`; commit additionally retries `23505`
  after an invisible concurrent insert following a SERIALIZABLE advisory-lock
  wait. The staged variant uses a savepoint and deliberately does not roll back
  the caller's outer transaction (`app/core/payments/engine.py:245-348`).
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
- Commit-only `23505` after an invisible concurrent insert: preserve the existing
  whole-commit retry so the terminal `tx_id` state resolves idempotently.
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
- commit-only `23505` retry after a SERIALIZABLE lock wait has deterministic real
  PostgreSQL characterization in addition to its predicate unit guard;
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
