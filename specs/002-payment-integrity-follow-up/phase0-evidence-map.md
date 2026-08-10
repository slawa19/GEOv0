# Phase 0 evidence map

Date: 2026-08-10

Baseline: `296719d9055c14f6b463ddb7d8a3651c88087d76`

Branch: `codex/payment-integrity-follow-up-phase0`

Decision: confirmed P2; Phase 1 is specified but not authorized

## 1. Baseline and history

The branch started from the accepted program-001 governance head with a clean
tracked tree and no ahead/behind delta. Program 001 had already recorded the
reverse-direction lock question as residual P2 rather than an accepted defect.

History evidence:

- `8de9f3e` introduced the directed identity and the reverse-inequality unit
  expectation as part of the original multipath oversubscription work.
- `34371fb` extended segment locking to single-route prepare.
- `a334c46` extended the tx/segment protocol to commit and abort.
- `684e8eb` added a cumulative advisory-lock budget.
- `f5a86ae` selected explicit `SERIALIZABLE` for the same-tx duplicate-commit test.
- `5771193` first recorded reverse-direction locking as a Phase 7 residual.

These commits explain the mechanism's evolution; they do not prove the reverse
behavior correct.

## 2. Source evidence

| Claim | Evidence |
|---|---|
| Segment identity is directed | `app/core/payments/engine.py:82-93` |
| Reverse keys are intentionally unequal in current unit coverage | `tests/unit/test_payment_engine_advisory_lock_key.py:13-18` |
| One call sorts/deduplicates its supplied keys | `app/core/payments/engine.py:115-150` |
| Retry handles `40P01`/`40001`; staged retry uses a savepoint | `app/core/payments/engine.py:263-348` |
| One flow mutates reciprocal debt state | `app/core/payments/engine.py:1182-1255` |
| Staged payment is an explicit service path | `app/core/payments/service.py:221-250` |
| Real simulator invokes staged payment | `app/core/simulator/real_payments_executor.py:369` |
| Production PostgreSQL default is SERIALIZABLE | `app/config.py:68`, `app/db/session.py:58-63` |
| Clearing canonicalizes pairs for its snapshot | `app/core/clearing/service.py:447-472` |
| Clearing locks debts before reading PrepareLocks | `app/core/clearing/service.py:827-876` |

## 3. Existing-test coverage

Existing PostgreSQL tests prove:

- prepare versus commit blocking on the same directed segment;
- same-`tx_id` commit/abort/duplicate-prepare serialization;
- same-direction shared-bottleneck reservation;
- idempotency request uniqueness and publication;
- one clearing/payment row-lock and version-preservation schedule.

They do not prove:

- two distinct transaction IDs using `A -> B` and `B -> A`;
- reverse multi-segment lock-set order;
- a real sibling-payment `40P01`/`40001` schedule;
- real savepoint retry with a pre-savepoint transaction advisory lock;
- PostgreSQL cancellation of a reverse/staged payment;
- one shared advisory identity between clearing and payments.

`tests/integration/test_payment_engine_uow_retry_postgres.py:14-181` injects a
synthetic `40001` at `session.commit`; it proves orchestration but not a real
serialization schedule. `tests/unit/test_payment_engine_retry_savepoint_nocommit.py`
uses a fake deadlock and cannot prove transaction-lock lifetime across savepoints.

## 4. Canonical command evidence

### Narrow unit gate

```powershell
$selectors = @(
  'tests/unit/test_payment_engine_advisory_lock_key.py',
  'tests/unit/test_payment_engine_advisory_locks_execute.py',
  'tests/unit/test_payment_engine_retry_savepoint_nocommit.py',
  'tests/unit/test_payment_timeouts.py',
  'tests/unit/test_payments_2pc.py',
  'tests/unit/test_recovery_cleanup.py'
)
.\scripts\verify_local.ps1 -TaskSlug phase0_payment_lock_unit_root `
  -BackendOnly -BackendSelector $selectors
```

Result: exit `0`, `45 passed`.

### Existing PostgreSQL gate

Database was manually verified as the unique disposable database
`geov0_test_phase0_payment_lock_root` on loopback port `55432` before setting
`GEO_TEST_ALLOW_DB_RESET=1`.

```powershell
$selectors = @(
  'tests/integration/test_payment_commit_advisory_locks_postgres.py',
  'tests/integration/test_payment_engine_uow_retry_postgres.py',
  'tests/integration/test_payment_idempotency_postgres.py',
  'tests/integration/test_concurrent_clearing_payment_lost_update_postgres.py',
  'tests/integration/test_concurrent_prepare_routes_bottleneck_postgres.py'
)
.\scripts\verify_local.ps1 `
  -TaskSlug phase0_payment_lock_pg_existing_root_foreground `
  -BackendOnly -BackendMarker postgres -BackendSelector $selectors
```

Result: exit `0`, `10 passed`.

Earlier attempts returned connection/shutdown errors while the WSL-hosted database
process did not remain alive. Those were infrastructure-negative runs, not product
test failures and not green evidence. The successful run used a foreground-owned
PostgreSQL 16 container.

## 5. Temporary PostgreSQL reproducer

The reproducer lived only under the ignored task output root and is not a proposed
tracked test. It used real SERIALIZABLE sessions, barriers after the first flow or
lock acquisition, two route start orders, distinct transaction IDs and assertions
over durable state.

Observed result, exit `0`:

| Scenario | Observation |
|---|---|
| Direct reverse acquisition | Reverse key differed and acquired while forward key was held. |
| Inverse multi-flow commit, left first | Real `40P01`; retry completed both transactions. |
| Inverse multi-flow commit, right first | Real `40P01`; retry completed both transactions. |
| Durable results | Both states `COMMITTED`; reciprocal debts `9.00000000`; zero PrepareLocks; two audit rows. |
| Staged inverse acquisition | With a deliberate four-attempt zero-delay override, one outer transaction committed; the other received `40P01` on every attempt and failed. Production defaults to three attempts. |
| Lock timeout | PostgreSQL lock timeout mapped to `asyncio.TimeoutError`. |
| Cancellation | `CancelledError` propagated; the lock was acquirable after rollback. |

The repeated staged deadlock is mechanistically expected: each outer transaction
retains its first `pg_advisory_xact_lock`; rollback of the retry savepoint cannot
release that pre-savepoint transaction lock. Local sorting inside the second call
cannot repair the already inverted global order.

## 6. Severity decision

### Confirmed in-scope P2

Reverse-direction payments do not share the advisory identity of the reciprocal
debt resource. This creates avoidable row deadlocks and retry-budget consumption.
For staged multi-call owners it creates a deterministic failure of one outer UoW
under inverse acquisition order. It is user/operationally material and merits a
delivery phase.

### Why this is not a confirmed in-scope P1

Under the checked SERIALIZABLE, transaction-owned scenario, whole-UoW retry
preserved exact terminal states and monetary/audit counts. The staged loser rolled
back rather than leaving a partial effect. No double application, trust-limit
violation or durable partial route was observed.

### Independent P2 registration — not program 002 scope

`ClearingService.execute_clearing_with_amount` commits directly at
`app/core/clearing/service.py:1079`, while real-simulator progress and publication
occur only after the service returns. A read-only audit reproduced a commit that
became durable and then raised `CancelledError`: the clearing transaction was
durably `COMMITTED`, but the caller observed cancellation and did not publish the
cycle result. This confirms an ambiguous durable-commit and runtime-publication
boundary. It does not yet demonstrate a duplicate clearing, monetary corruption
or a supported caller retry, so Phase 0 does not elevate it to P1. Program 001
likewise classified analogous cancelled durable audit/publication outcomes as P2
(`specs/001-codebase-renovation/spec.md:433-441`).

Owner: clearing service plus simulator commit-resolution/publication.

Disposition: register for a separate owner-approved program; do not fix or expand
program 002. No tracked reproducer or product edit was made in Phase 0.

## 7. Other residuals and scope disposition

- **Accepted in-program P2:** clearing and payment do not share an advisory domain;
  the current PrepareLock snapshot has a TOCTOU window. The active contract says
  clearing avoids active prepared pairs
  (`docs/ru/simulator/backend/payment-integration.md:70`), and Phase 2 owns this
  bounded serialization/revalidation boundary.

Recorded outside program 002:

- clearing early `None` paths can retain caller transaction/row locks;
- real simulator may publish a stale candidate clearing amount instead of the
  row-locked actual amount;
- trust growth mutates in-memory history/scenario before commit;
- recovery can issue redundant lock cleanup after `abort` already cleaned it.

Only the explicitly accepted clearing/payment P2 overlaps this program's resource
boundary. The remaining items are recorded evidence, not silently authorized
backlog.

## 8. Unverified paths

- non-default PostgreSQL isolation levels;
- production-scale starvation and retry telemetry;
- process loss at commit acknowledgement rather than controlled exception paths;
- mixed-version deployment using old and canonical lock identities;
- full real-simulator tick replay after a staged deadlock;
- clearing versus a newly prepared reverse payment under the target protocol.

These are explicit Phase 1/2 acceptance inputs or separately owned residuals; no
claim of full payment/clearing correctness is made by Phase 0.

## 9. Independent audit ledger

Three read-only agents separately covered:

1. payment lock identity, call-sites and transaction ownership;
2. recovery/clearing/cancellation and sibling risks;
3. PostgreSQL tests, git history and documentation contracts.

The orchestrator independently reran the canonical unit and PostgreSQL selectors
and the real reproducer. Agent reports were treated as leads rather than gates.

The separate read-only adversarial review examined exact range
`296719d9055c14f6b463ddb7d8a3651c88087d76..f294c01`. It found no P1, four
documentation P2s and one citation P3. The orchestrator confirmed each P2 against
the implementation and active contracts before remediation:

- mixed-version compatibility now requires canonical plus both directed legacy
  keys, or coordinated quiescence;
- the independent clearing ambiguous outcome is P2, not an evidenced P1;
- the owner inventory now includes Admin staged abort and both recovery loops;
- the clearing/payment snapshot race is explicitly accepted as the Phase 2 P2.

Reviewer checks: exact-range `git diff --check` exit `0`; changed-surface and
relative-link scans exit `0`; targeted unit selector exit `0` (`6 passed`). No
files were edited by the reviewer.

External Claude review results are appended during Phase 0 closeout; their absence
means the phase is not yet closed.

## 10. Initial external review

Claude Code `2.1.226` reviewed frozen range
`296719d9055c14f6b463ddb7d8a3651c88087d76..c2a0974e158f064dbd6da89f1e78d5a2e2a3622d`
from a clean credential-free standalone clone. Command policy was `opus`, effort
`high`, plan permissions, and disallowed `Edit,Write,NotebookEdit`. Result: exit
`0`, complete JSON, `is_error=false`, resolved model `claude-opus-5`.

The reviewer confirmed the docs-only scope, all cited call-sites/history and the
P2 severity decisions. It reported three documentation findings, all manually
checked before remediation:

1. disclose the reproducer's deliberate four-attempt override versus the
   production default of three;
2. separate the accepted clearing P2 from the rendered out-of-scope residual list;
3. preserve the existing commit-only `23505` idempotency retry in the Phase 1
   failure and acceptance matrix.

The fix-delta receives the single remediation review allowed by the orchestrator
rule before Phase 0 closeout.
