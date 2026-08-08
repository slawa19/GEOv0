# Phase 2 backend owner/effect map

Status: **COMPLETE** (2026-08-08). This is a dated evidence ledger for
REN-010A at base `0635a651f0ae6f970d82d7d71b7a18071069262d`; the accepted product
HEAD is `7d0a8a9ca48cec34cc62e0965cdd6d28825370de`. It does not replace the
REST schema, domain documentation, code, or current gate results.

## Confirmed hypotheses

| Investigation target | Current behavior at the Phase 2 base | Intended behavior | Optimal target and decision |
|---|---|---|---|
| Public trustline cache | Create/update/close committed without invalidating `PaymentRouter` graph/topology caches. A behavioral reproducer observed stale capacity after commit. | Successful trustline mutations must be visible to subsequent routing; audit is explicitly best-effort in `docs/ru/02-protocol-spec.md:341,362,381`. | **FIX** exact-equivalent cache invalidation after commit. **KEEP** best-effort public trustline audit; do not impose the mandatory Admin/operator audit contract on participant actions. |
| Clearing outcome taxonomy | `ClearingService.execute_clearing_with_amount` returned `None` for both expected candidate/lock/policy skips and unexpected mutation/invariant/commit failures. | Clearing is one DB transaction and rolls back on error (`docs/ru/09-decisions-and-defaults.md:80`); internal failures use sanitized `E010`. | **FIX** expected skips remain `None`; unexpected execution failures roll back and surface generic `E010`. Checkpoint/audit construction remains explicit, logged best-effort observation. |
| Concurrent duplicate payment request | Initial lookup rejected a matching nonterminal row, but the unique-key collision path converted that same row through the terminal result mapper and could report synthetic `ABORTED`. | Same `tx_id` plus a different canonical request conflicts; a matching terminal repeat is idempotent (`docs/ru/09-decisions-and-defaults.md:133`, `docs/ru/02-protocol-spec.md:2155`). | **FIX** one existing-payment classifier for initial lookup and insert-race reload; matching nonterminal rows return typed `E008/409`, terminal rows retain idempotent replay. |

## Write-path ownership and effects

| Supported path | Caller → transaction owner | Durable boundary and outward effects | Decision |
|---|---|---|---|
| Public payment create/execute/repeat | `app/api/v1/payments.py` → `PaymentService` application owner → `PaymentEngine` prepare/commit/abort stage owners | Transaction row and engine stages commit through the existing 2PC-like lifecycle. Routing caches are invalidated after successful stages; `payment.received` publication is best-effort after a committed result. | **FIX** only the reproduced insert-race classifier. **KEEP** lifecycle facade, stage ownership, terminal repeat, cache and publication boundaries. |
| Clearing preview | Public or Simulator caller → `ClearingService.find_cycles` | Read-only; no commit, audit, cache mutation, or publication. | **KEEP**. |
| Clearing execute/auto | Public or Simulator caller → `ClearingService.execute_clearing_with_amount` | Debt changes, `CLEARING` transaction and in-transaction integrity audit commit together. Exact-equivalent routing cache invalidates after commit; caller publishes Simulator SSE only after a successful result. | **FIX** unexpected-failure taxonomy. **KEEP** no-cycle/lock/policy skips and algorithm. |
| Public trustline create/update/close | `app/api/v1/trustlines.py` → `TrustLineService` | Service commits mutation plus best-effort `IntegrityAuditLog`; exact-equivalent graph and topology caches invalidate only after commit. No SSE publication. | **FIX** post-commit cache boundary. **KEEP** service facade and documented best-effort audit. |
| Admin participant status | `app/api/v1/admin.py` route | Participant mutation and mandatory `AuditLog` share one route-owned commit; failure/cancellation rolls back both. | **FIX** prior split commits. |
| Admin equivalent create/update/delete | `app/api/v1/admin.py` route | Equivalent mutation and mandatory `AuditLog` share one route-owned commit; response is materialized before commit; failure/cancellation rolls back both. | **FIX** prior split commits. |
| Admin transaction abort | `app/api/v1/admin.py` route → `PaymentEngine.abort(commit=False)` inside the route-owned transaction | Engine stages abort under its lock/savepoint; route commits staged state plus mandatory `AuditLog` once. `COMMITTED` is a conflict before/after the engine lock, never a false `aborted` response. | **FIX** split audit and misleading committed-abort success. No separate Admin lost-update matrix case was confirmed. |
| Integrity net mutual debts | `app/api/v1/integrity.py` route | Repair and mandatory Admin `AuditLog` share one commit. HTTP response is the only publication. Netting preserves `(limit - forward debt) + reverse debt`, so routing capacity is unchanged. | **FIX** missing durable Admin audit. **KEEP** cache and HTTP-only publication. |
| Integrity cap debts to trust limits | `app/api/v1/integrity.py` route | Repair and mandatory Admin `AuditLog` share one commit; only actually affected equivalent caches invalidate after commit. HTTP response is the only publication. | **FIX** missing durable Admin audit and stale cache. **KEEP** HTTP-only publication. |
| Simulator trustline create/update/close | `app/api/v1/simulator.py` route | Route owns commit; runtime topology mutation, exact-equivalent cache invalidation and SSE are post-commit best-effort effects. | **KEEP**; public-service cache defect is not duplicated here. |
| Simulator real payment | `app/api/v1/simulator.py` → `PaymentService(commit=True)` | Payment service reaches a durable terminal result before the route emits best-effort `tx.updated`. | **KEEP**. |
| Simulator real clearing | `app/api/v1/simulator.py` → `ClearingService` per cycle | Service commits each successful cycle; route emits best-effort `clearing.done` only after completed cycles. Unexpected service failures now surface instead of becoming zero cleared cycles. | **KEEP** caller boundary; shared clearing taxonomy **FIX** applies. |

`REN-012A` is **NO TRIGGER**: the confirmed defects are local to existing service
or route seams; no repeated Simulator transaction-owner problem requires a new
backend adapter.

## Actor/owner matrix

| Actor | Accepted boundary | Evidence-led decision |
|---|---|---|
| Anonymous public-domain caller | Payment/trustline/clearing participant routes reject without credentials. | **KEEP**. |
| Active participant access token | Resolves an existing active participant; participant-owned routes are allowed. | **KEEP**. |
| Inactive participant or refresh token at an access-token route | Inactive participant is forbidden; refresh token is rejected as the wrong token type. | **KEEP**. |
| Admin token | Admin routes require the configured Admin token (except the explicit dev allowlist); Simulator resolves Admin before participant/cookie identity. | **KEEP**. |
| Anonymous Simulator cookie | Resolves one anonymous owner; state-changing requests enforce the configured Origin/CSRF boundary and cross-owner run access is denied. | **KEEP**. |
| Challenge and refresh lifecycle | Sequential challenge use and refresh rotation/type enforcement are covered. | **KEEP** verified behavior. Concurrent consume remains **UNVERIFIED / NO FIX** because no production-like store race was reproduced; no auth-platform expansion is authorized. |

## Bounded PostgreSQL exit matrix

| Required case | Exact selector | Required observations | Status |
|---|---|---|---|
| Two payments, one constrained capacity | `tests/integration/test_concurrent_prepare_routes_bottleneck_postgres.py::test_concurrent_payments_shared_bottleneck_commit_once_postgres` | One commit and one terminal rejection; debt within capacity; no locks; one payment audit/publication. | **PASSED live PostgreSQL** — run `31256289008`, job `93100006720` |
| Payment versus clearing, same trustline | `tests/integration/test_concurrent_clearing_payment_lost_update_postgres.py::test_concurrent_payment_and_clearing_same_trustline_preserve_effects_postgres` | Both durable effects preserved; debt within limit; terminal transactions, audits, no locks, one payment publication. | **PASSED live PostgreSQL** — run `31256289008`, job `93100006720` |
| Concurrent duplicate payment `tx_id` | `tests/integration/test_payment_idempotency_postgres.py::test_concurrent_duplicate_payment_request_never_regresses_terminal_state_postgres` | One transaction/effect/audit/publication; loser receives `E008/409`; terminal state never regresses. | **PASSED live PostgreSQL** — run `31256289008`, job `93100006720` |
| Admin lost update | Conditional only when reproduced. | No qualifying race was confirmed by the owner map. | **NOT TRIGGERED** |

Collect-only and SQLite regression are not substitutes for this live matrix.

## Exit evidence

The published [workflow run
31256289008](https://github.com/slawa19/GEOv0/actions/runs/31256289008)
executed on exact product SHA `7d0a8a9ca48cec34cc62e0965cdd6d28825370de`.
Its guarded disposable PostgreSQL job passed the three-case Phase 2 matrix (`3
passed`) and the complete registered PostgreSQL marker tier (`11 passed, 105
deselected`). The conditional Admin lost-update matrix case remained **NOT
TRIGGERED** because the owner map found no qualifying lost-update boundary.

The same run's required local-equivalent job passed backend (`735 passed, 2
skipped, 15 deselected`), Admin (`76 passed` plus build), and Simulator (`637
passed` plus build) gates. The overall workflow conclusion is still `failure`,
not “CI green”, because the scheduled Admin and Simulator visual E2E jobs retain
known failures owned by later frozen phases.

Final internal adversarial review findings were fixed in bounded commits
`8936031`, `8a1601f`, `c9a34cc`, and `7d0a8a9`; independent re-reviews found no
remaining P1/P2. Claude Code `2.1.226` reviewed the full
`0635a651..7d0a8a9` range read-only at high effort with exit `0`, complete JSON,
`is_error=false`, and resolved `claude-opus-5`. Its five findings were manually
triaged; none survived as a P1/P2 after call-site, sanitization, nested-transaction,
and logging-boundary verification. No generalized UoW framework or broad module
rewrite was introduced, and Phase 3 has not started.
