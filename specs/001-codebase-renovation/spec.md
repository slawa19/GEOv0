# GEOv0 Codebase Renovation Specification

- **Date:** 2026-08-07
- **Status:** PHASE 6 COMPLETE — PHASE 7 PAUSED
- **Plan:** `specs/001-codebase-renovation/plan.md`
- **Backlog:** `specs/001-codebase-renovation/tasks.md`
- **Status authority:** this label is descriptive only and is not authoritative. Completion is established only by the success criteria, recorded evidence, and accepted review artifacts defined below. A future edit that changes `Status` without that evidence does not make the renovation complete.
- **Owner:** repository maintainers, with one named wave owner and one independent reviewer recorded before each implementation wave starts.

## Owner surfaces

This specification covers the complete maintained repository surface:

- runtime and domain code: `app/`, including API, schemas, DB models/session management, payments, clearing, integrity, simulator, auth, recovery, observability and utilities;
- public contracts and persistence: `api/`, `migrations/`, `fixtures/`, `admin-fixtures/`, `seeds/`;
- active frontends: `admin-ui/` and `simulator-ui/v2/`;
- legacy/reference surface: `simulator-ui/v1/` and explicitly archived specifications, until a documented keep/archive/delete decision is made;
- verification: `tests/`, frontend unit tests, Playwright suites, fixture validators, local verification scripts and CI configuration;
- delivery and operations: Dockerfiles, Compose files, launch/seed/migration scripts, environment examples and runbooks;
- governance and documentation: root contributor/agent instructions, `README.md`, `docs/`, and this `specs/001-codebase-renovation/` package.

Generated environments, local databases, logs, caches and test output are cleanup targets, not product surfaces.

## Problem

The repository was developed through many incremental model-driven changes. It contains valuable tests and defensive code, but its intended architecture, executable architecture, contracts, verification gates and documentation are not consistently aligned. The problem is therefore not simply large files or style: ownership and invariants are difficult to trace across modules, while some current checks provide weaker guarantees than their names imply.

## Project profile and proportionality boundary

This renovation is calibrated for the actual GEOv0 product, not for a regulated
banking platform or a high-availability distributed service:

- GEOv0 is a v0.1-alpha/MVP community hub and simulator; the documented MVP scale
  is approximately 10–500 participants in one community.
- The hub coordinates and records mutual-credit operations but is not a bank or
  custodian. Payment, trustline, clearing, ownership and audit invariants still
  matter because silent double application or contradictory state would destroy
  the usefulness of the MVP.
- The required production-like proof is one documented application topology with
  PostgreSQL, not multi-region, active-active or formal disaster-recovery proof.
- SQLite remains useful for local and fast tests, while a small, selected
  PostgreSQL suite proves the semantics that SQLite cannot: row/advisory locking,
  concurrent writes and schema migrations.
- Frontend acceptance targets the supported Chromium path and keyboard access to
  critical workflows. It is not a WCAG certification, multi-browser matrix or
  visual redesign.
- Refactoring is justified by a reproduced defect, a protected invariant, a
  recurring ownership problem or a clearly cheaper maintenance boundary. File
  size, style preference and hypothetical future scale are not sufficient.

The plan and backlog are frozen after review. New P3 findings do not expand the
program. A new P1 must be tied to a supported user flow or data/security risk; a
new P2 is mapped to an existing slice or recorded as residual debt.

The findings below are the dated baseline captured when this specification was
created. Their current disposition is tracked in `tasks.md`; a path removed or a
working-tree fix does not become accepted evidence until its named gates complete.

### Baseline confirmed P1 findings

1. **The default Compose/config combination can run with development security semantics and placeholder secrets.** `Settings.ENV` defaults to `dev`, and production secret/origin guardrails are skipped for dev/test (`app/config.py:44-46`, `app/config.py:151-221`). The base Compose service supplies `JWT_SECRET: change-me-in-production` but does not set `ENV`, `ADMIN_TOKEN`, or `SIMULATOR_SESSION_SECRET` (`docker-compose.yml:37-45`). The development override sets `ENVIRONMENT=dev`, while the application reads `ENV` (`docker-compose.dev.yml:12-16`, `app/config.py:44-46`). This is a configuration mismatch, not proof of an exploited deployment.
2. **The simulator owner schema differs between ORM and PostgreSQL migration.** ORM declares `owner_id` nullable and omits the owner composite index (`app/db/models/simulator_storage.py:19-21`, `app/db/models/simulator_storage.py:42-53`), while migration 017 makes `owner_id` non-null on PostgreSQL and creates `ix_simulator_runs_owner_state_created` (`migrations/versions/017_add_owner_to_simulator_runs.py:39-49`).
3. **The OpenAPI drift test checks paths and methods, not request/response/security schema equivalence.** `tests/contract/test_openapi_contract.py:42-86` explicitly limits enforcement to paths and methods.
4. **Background-job startup and periodic integrity failures can be silent.** Recovery task creation exceptions are swallowed (`app/main.py:109-120`); integrity-loop execution and task creation exceptions are also swallowed (`app/main.py:170-182`). A configured subsystem may therefore be absent without an actionable readiness signal.
5. **Payment transaction ownership is split across API/service/engine layers.** `PaymentService` conditionally commits or uses nested transactions and delegates to engine methods that also accept `commit=` (`app/core/payments/service.py:401-560`; related commit/rollback sites exist throughout `app/core/payments/engine.py`). This makes the unit-of-work boundary context-dependent and increases the cost of proving atomicity and failure semantics. The target UoW design is not yet selected.
6. **Admin UI list and graph loading can apply stale asynchronous results.** Participants and Trustlines launch overlapping loads from route/page/filter watchers without request-generation or abort ownership (`admin-ui/src/pages/ParticipantsPage.vue:100-125`, `admin-ui/src/pages/ParticipantsPage.vue:192-223`, `admin-ui/src/pages/TrustlinesPage.vue:130-156`, `admin-ui/src/pages/TrustlinesPage.vue:172-203`). In the graph EQ path, `refreshSnapshotForEq()` applies data before the caller's request-id check (`admin-ui/src/composables/useGraphData.ts:200-219`, `admin-ui/src/pages/graph/useGraphPageWatchers.ts:74-85`).
7. **Frontend and repository gates are not automated.** `.github/workflows/` is absent. `scripts/verify_local.ps1:10-16` covers Admin unit tests and build but omits lint, e2e and Simulator UI. Simulator v2 defines typecheck/unit/build/e2e but no lint script or ESLint dependency (`simulator-ui/v2/package.json:6-36`); its existing ESLint config covers only window-manager bridging (`simulator-ui/v2/eslint.config.js:1-19`).
8. **Simulator UI's primary interaction is pointer-only.** The main canvas handles pointer/click/wheel events but has no keyboard semantics or accessible name, and the FX canvas is not explicitly hidden from assistive technology (`simulator-ui/v2/src/components/SimulatorAppRoot.vue:1012-1025`).

### Baseline confirmed P2 findings

1. **Oversized orchestration modules mix responsibilities.** Examples include `app/api/v1/simulator.py` (2620 lines), `app/api/v1/admin.py` (1947), `app/core/payments/engine.py` (1223), `app/core/clearing/service.py` (1086), `simulator-ui/v2/src/composables/useSimulatorApp.ts` (1805), `SimulatorAppRoot.vue` (1300), and `useSimulatorRealMode.ts` (1249). Size alone is not a defect, but these files combine routing, validation, transaction/lifecycle policy, state mutation, transport or presentation. Simulator's own standard says composables over 150 lines or over three lifecycle/watch hooks require decomposition (`docs/ru/development-standards.md:610-617`).
2. **`QueueFull` is caught outside the callback where it can occur.** `call_soon_threadsafe(sub.queue.put_nowait, message)` schedules the operation, but the surrounding `try` only covers scheduling (`app/utils/event_bus.py:43-53`). Backpressure loss is therefore neither reliably caught nor measured.
3. **Simulator concurrency and lock/lease behavior need explicit proof.** Current code mixes per-process locks, DB isolation/row locks, optimistic versions and retry behavior. For example seeding uses an in-process per-run lock (`app/api/v1/simulator.py:400-462`), while payment/clearing have separate transaction and lock paths. This is a review finding, not a decision to replace those mechanisms.
4. **Raw lower-level exception text can be converted into a client-facing 400.** Payment prepare failures become `BadRequestException(f"Payment preparation failed: {str(e)}")` (`app/core/payments/service.py:464-496`), conflating operational faults and user input errors and potentially exposing implementation details.
5. **Observability has silent and best-effort gaps.** Besides background-task swallowing, event-bus drops are not counted (`app/utils/event_bus.py:43-53`), and several metrics/logging blocks suppress exceptions. The review must distinguish intentional best effort from required health signals.
6. **Admin API contracts are uneven.** `realApi.ts` validates some domain payloads with Zod (`admin-ui/src/api/realApi.ts:33-118`) but leaves config/health/integrity as `Record<string, unknown>` (`admin-ui/src/api/realApi.ts:562-618`); mock code includes broad casts such as `as unknown as T` (`admin-ui/src/api/mockApi.ts:322`).
7. **Simulator frontend ingress is only partially validated.** HTTP generics cast `res.json()` directly to `T` (`simulator-ui/v2/src/api/http.ts:44-68`); unknown SSE events are cast into a catch-all union (`simulator-ui/v2/src/api/normalizeSimulatorEvent.ts:367-369`, `simulator-ui/v2/src/api/simulatorTypes.ts:308-315`). `useSimulatorRealMode.ts` combines SSE replay, reducer-like snapshot mutation, counters, timers, labels and visual effects (`simulator-ui/v2/src/composables/useSimulatorRealMode.ts:504-680`, `simulator-ui/v2/src/composables/useSimulatorRealMode.ts:853-898`).
8. **Frontend delayed work lacks a consistent lifecycle contract.** Admin's debounce supports cancellation (`admin-ui/src/utils/debounce.ts:1-22`), but page consumers do not cancel on unmount; throttle has no cancellation (`admin-ui/src/utils/throttle.ts:5-23`).
9. **Tests contain false-signal and portability risks.** `tests/test_e2e_example.py:23-100` contains placeholder/pass tests; their fixtures are explicitly placeholders (`tests/conftest.py:226-260`). Test DB and basetemp use fixed repository paths (`tests/conftest.py:26-29`, `pytest.ini:20-24`), which impedes safe parallelism. The OpenAPI limitation is noted above. Simulator visual baselines are Windows-specific (`simulator-ui/v2/e2e/scenes.spec.ts-snapshots/*-win32.png`), and some tests verify source-text wiring rather than behavior.
10. **Repository hygiene and source-of-truth boundaries are unclear.** Tracked files include root `.tmp_*` outputs and `simulator-ui/v2/test_output.txt`; the latter is an ANSI-bearing test log (`simulator-ui/v2/test_output.txt:2-30`). `admin-ui/src/components/HelloWorld.vue:1-3`, `admin-ui/src/assets/vue.svg`, and the Vite favicon/title in `admin-ui/index.html:5-7` are starter remnants. `simulator-ui/v1/` remains runnable despite being marked legacy (`simulator-ui/README.md:3-6`).
11. **Documentation has competing source-of-truth claims and stale active/archive material.** Configuration, generated FastAPI OpenAPI versus `api/openapi.yaml`, translated documents, active specifications and archived specifications are not governed by a single precedence rule. The manual-operations specification exists in active and historical narratives with completion statements that must be verified against code rather than copied forward.
12. **Deployment definitions are duplicated.** Base Compose builds `docker/Dockerfile` (`docker-compose.yml:30-34`), the dev override switches to root `Dockerfile` (`docker-compose.dev.yml:5-11`), and the two images have different entrypoint/migration/health behavior. `docker/docker-entrypoint.sh:59-66` runs Alembic then Uvicorn, while the root Dockerfile uses a direct CMD (`Dockerfile:48-53`).

### Credential audit note

The orchestrated audit found no tracked or historical credential requiring removal; the only noted credential is embedded in a configured origin and the owner explicitly chose to leave it. This renovation must not broaden that value's use, print it, rotate it, or rewrite history without a separate authorization. Secret scanning remains an anti-regression gate.

## Non-goals

- Do not make code match documentation merely because documentation exists; first establish which behavior and invariant are authoritative.
- Do not edit documentation to conceal code defects, and do not edit code to preserve obsolete prose.
- Do not perform edge-case polishing before P1 safety, contract, lifecycle and gate failures are controlled.
- Do not rewrite the backend, either UI, simulator, payment engine or persistence layer from scratch.
- Do not replace proven domain behavior with fashionable patterns without a measured benefit and migration path.
- Do not combine product feature work with renovation unless it is required to preserve an existing supported flow.
- Do not delete legacy code, tests, fixtures, migrations or specifications solely because they look old; require reachability, history and replacement evidence.
- Do not weaken, mass-update or delete tests to obtain a green run.
- Do not update visual snapshots without intentional visual review.
- Do not change public API, event, persistence or fixture contracts without compatibility and rollback decisions.
- Do not rotate credentials, rewrite Git history, deploy, or mutate production/
  external data under this specification. Publishing the approved branch and
  triggering GitHub Actions is allowed only after a separate recorded owner
  authorization naming the remote, branch and trigger; that authorization does not
  permit deployment or broader credential use.
- Do not design multi-region, active-active, automatic failover, formal RPO/RTO,
  bank-grade settlement or exhaustive fault-injection machinery for this MVP.
- Do not add RBAC/SSO, a new frontend state framework, an outbox/event-sourcing
  platform, generalized repository/UoW frameworks or generated API clients unless
  a separate product decision establishes that need.
- Do not require Firefox/WebKit/mobile matrices, full WCAG certification, formal
  performance benchmarking or full translation parity without a reproduced
  product need.
- Do not split large files to meet a line-count target. Extract only a boundary
  needed by an accepted behavioral or maintenance slice.

## Decision principles and value filter

Every proposed change is a hypothesis until its wave review records evidence and accepts it. A change proceeds only when it passes this filter:

1. **Protect value first:** security, accounting/integrity, transaction atomicity, ownership isolation and recoverability outrank cleanup aesthetics.
2. **One authoritative owner:** each transaction, state machine, schema, generated artifact, configuration key and background task has one explicit owner.
3. **Behavior before structure:** characterize current supported behavior before moving code. Structural work must preserve it unless an intentional behavior decision is recorded.
4. **Boundary validation:** validate untrusted/network/persistence inputs once at ingress; keep internal types narrow and avoid repeated defensive casts.
5. **Explicit failure semantics:** distinguish user rejection, conflict, timeout, overload, cancellation and internal failure. Never rely on parsing exception strings when a typed status/code can exist.
6. **Concurrency is a contract:** define ordering, idempotency, retry, cancellation, lock/lease duration and stale-result behavior before changing implementation.
7. **Prefer reversible slices:** small vertical changes with characterization tests and compatibility seams over cross-repository big-bang edits.
8. **Delete only with evidence:** no imports/runtime discovery, no supported contract, no required historical/reference value, and a passing gate after removal.
9. **Automate durable knowledge:** if an invariant matters after this project, encode it in schema, type, test, lint rule, CI gate or generated check.
10. **Measure simplification:** a refactor should reduce ownership ambiguity, dependency direction, duplicated policy or operational risk—not merely move lines.
11. **No speculative abstraction:** require at least two real consumers or one clear policy boundary before introducing a shared abstraction.
12. **Documentation follows accepted reality:** update canonical docs after the implementation/gates establish the result, while preserving rationale in ADR/spec history.

## Anti-regression requirements

- Establish a baseline manifest before edits: commit, environment/tool versions, commands, pass/fail/skip counts, known flaky tests, DB dialects and fixture hashes.
- Keep every wave independently reviewable and revertible; avoid combining cleanup, contract changes and behavior changes in one commit.
- Add characterization tests before modifying high-risk payment, clearing, simulator replay, auth or persistence behavior.
- Exercise PostgreSQL only for selected persistence/concurrency claims that depend
  on its semantics; SQLite success is not evidence for those claims. Do not build
  an exhaustive database/dialect matrix.
- Verify migrations from an empty PostgreSQL DB and the immediately supported
  previous revision. A formal downgrade rehearsal is required only if the project
  promises downgrade; otherwise document a forward-fix path.
- Verify OpenAPI payloads, errors and security declarations, not only path/method inventory.
- Preserve idempotency, accounting invariants, owner isolation, event ordering/replay and COMMITTED-terminal semantics.
- For async UI work, only the current request owner may apply data, error and loading state; timers/listeners/RAF/observers must be disposed.
- Preserve a keyboard-accessible route through the critical Admin graph and
  Simulator inspect/payment/trustline/clearing flows that are included in the
  frozen functional matrix; this is not a claim of complete accessibility.
- Preserve fixture determinism and use synchronization scripts; do not hand-edit generated public fixture copies.
- Maintain an explicit flaky/quarantine register. A quarantine needs owner, reason, issue and expiry; skipped/placeholder tests do not count as coverage.
- Treat secret scan and production-config preflight as gates; dependency
  vulnerability checks are a bounded diagnostic unless a supported manifest has a
  known critical advisory. Do not print sensitive values in logs/artifacts.
- Roll back by reverting the wave, restoring compatibility adapters and re-running the pre-wave gates. Database changes require a tested forward-fix or downgrade plan before execution.

## Success criteria

The renovation is complete only when all of the following bounded criteria have
linked evidence. A required PostgreSQL, published-CI, production-like-container or
scoped-Chromium result cannot be waived as `UNVERIFIED`; if it is unavailable, the
program remains incomplete until the owner explicitly changes this specification's
scope. Optional diagnostics may be reported as `UNVERIFIED`, never as passing.

1. One canonical local verifier and one published CI workflow complete for the
   backend, Admin UI and Simulator UI; required versus scheduled diagnostics are
   explicit and no placeholder test counts as success.
2. The supported dev path and one production-like Compose/image path have explicit
   configuration, secret guardrails, startup, readiness and shutdown behavior.
3. A disposable PostgreSQL run proves empty-schema migration, upgrade from the
   immediately supported revision and the small concurrency matrix named in the
   plan. No HA/failover claim is required.
4. Current payment, clearing, trustline, destructive Admin and Integrity-repair
   write paths have a recorded transaction-owner map. Only confirmed ambiguous/
   high-risk boundaries are changed, and their success/rejection/rollback/
   cancellation behavior is covered at the appropriate layer.
5. The maintained OpenAPI operations and the Simulator events used by the frozen
   functional matrix have backend-to-consumer validation. Unknown/malformed input
   is deterministic and cannot silently mutate trusted state.
6. Required background jobs and event-bus overload expose actionable readiness,
   logging or metrics without requiring a new durable messaging architecture.
7. Confirmed Admin overlapping loaders cannot apply stale data/error/loading after
   a newer request or unmount; critical Admin mutations use validated contracts and
   observable success/failure/audit behavior.
8. Simulator real-mode ingress, replay/dedup and state/effect ordering for the
   selected lifecycle/topology/payment/clearing event families are testable behind
   the existing facade. Full decomposition of all composables is not required.
9. The critical Admin graph and Simulator inspect/payment/trustline/clearing paths
   have a practical keyboard route, focus/status/error semantics and one scoped
   Chromium smoke. This is not a certification claim.
10. Canonical run/test commands write mutable DB/log/PID/test output below the
    designated ignored runtime root. Tracked garbage and proven starter remnants
    are removed; `simulator-ui/v1` receives an explicit read-only/archive decision
    without mandatory deletion.
11. Current front-door, testing, deployment, contract and affected UI architecture
    documents describe the verified result. Archives and untranslated copies are
    labelled; exhaustive documentation/translation rewriting is not required.
12. Internal adversarial review plus Claude Code Opus 5 / High review of each
    high-risk product batch leave no confirmed unresolved P1/P2 inside the frozen
    scope. High-risk means a change to payment/clearing/integrity transaction
    semantics, persistence/migration/security behavior or a protected REST/SSE
    contract. Other UI/cleanup/docs batches require independent internal review.
    Remaining lower-value debt is recorded without reopening the program.

## Risks

- **Behavior loss during decomposition:** large modules contain implicit sequencing. Mitigation: characterization tests, one seam at a time, trace comparison and wave rollback.
- **Accounting/concurrency regression:** moving commits or locks can create double-apply, lost update or false abort. Mitigation: explicit UoW decision, Postgres contention tests and failure injection before implementation.
- **Schema rollout failure:** tightening nullability/indexes can break existing data. Mitigation: inventory/backfill/preflight, staged migration and rollback/forward-fix rehearsal.
- **Security lockout:** correcting environment/secret guards can break local workflows. Mitigation: separate dev profile with explicit names and a tested migration note; never weaken production guardrails to preserve convenience.
- **Contract breakage:** stricter validation may expose existing producer drift. Mitigation: observe/report mode, captured fixtures and compatibility window before rejection where safe.
- **UI timing regressions:** request cancellation and pipeline separation can change loading, animation or replay ordering. Mitigation: deterministic clocks, reverse-resolution tests and real-mode sequence tests.
- **False confidence from green tests:** placeholders, source-text tests and single-dialect suites can pass without behavior proof. Mitigation: test-value audit and mutation/failure-oriented assertions for critical invariants.
- **Scope explosion:** comprehensive review can become endless. Mitigation: wave exit criteria, P1-first order, value filter and explicit deferral register.
- **Premature deletion:** old fixtures/specs may be hidden references. Mitigation: imports, runtime discovery, docs and Git history checks before removal.
- **Documentation churn:** translating unstable documents multiplies drift. Mitigation: update canonical source first; translate only after acceptance and mark generated/lagging copies.

## Changelog

- **2026-08-09:** Phase 5 completed locally at exact product-behavior commit
  `ff53dbc1c070220ad7ddfdaf8a0aa5c8c1ccd157`; final test evidence is
  `345991b47f15e2ed4080c7617f4f3430883f8b7b`. `phase5-simulator-map.md` records
  the accepted replay/state/effect seam, critical Simulator v2 keyboard paths,
  reviews, gates and residuals. Local Simulator gates passed lint, typecheck,
  `701/701` unit tests across `99` files, build and scoped non-visual Chromium
  `5/5`; the final test-only correction passed its `68/68` component selector and
  lint/typecheck. Internal adversarial review ended without a remaining product
  finding. Claude Code `2.1.226` reviewed the frozen product/remediation deltas
  read-only from clean credential-free standalone clones with exit `0`, complete
  JSON and resolved `claude-opus-5`; reproduced findings were fixed, and its sole
  final test-quality suggestion was resolved by the evidence-only commit. No
  published workflow ran, so this is not a “CI green” claim. Live-backend browser
  SSE, visual/browser-matrix, screen-reader and direct private-canvas callback
  probes remain unverified. No backend/OpenAPI/wire change or Phase 6 work occurred.
- **2026-08-08:** Phase 4 completed locally at exact product commit
  `f0428f9996e511e723ce1980a24c2073b3adadb8`; Phase 5 was not started. The
  loader classification, Current / Intended / Optimal decisions, selected
  operator paths, Graph ownership boundary, gates, reviews and residuals are
  recorded in `phase4-admin-map.md`. Deterministic mounted evidence now covers
  the frozen overlapping Admin loaders and pending unmounts; the selected
  operator contracts pass component/unit tests; Admin lint (`0` errors, `116`
  warnings), all `154` unit tests, build and scoped Chromium (`5/5`) passed. The
  disposable real-contract smoke passed `2/2` for config/flag audit and
  participant freeze/unfreeze with cleanup, and the confirmed audit timestamp
  selector passed `2/2`. Independent adversarial re-reviews were CLEAN. Claude
  Code `2.1.226` reviewed the frozen product/remediation deltas from standalone
  credential-free clones with exit `0`, complete JSON and resolved
  `claude-opus-5`; reproduced findings were fixed, and the final automatic
  re-search suggestion was rejected as contrary to the accepted guarded-graph
  invalidation policy. There is no new published workflow and no “CI green”
  claim. Replay ordering and REN-012B2/C remain Phase 5, invalid legacy
  Equivalent codes remain manual cleanup, and sibling participant/incident naive
  timestamps remain outside the one confirmed Phase 4 audit path.
- **2026-08-08:** Phase 3 completed at exact product commit
  `f8517bc90b119a3b156de0a2945019d1b9381118`; Phase 4 was not started. The
  selected Admin mutation/config and Simulator REST/SSE ingress contracts,
  decisions, residuals and review evidence are recorded in
  `phase3-contract-map.md`. Local backend validation on the last backend-changing
  commit `032ed86` passed `774` tests (`3` skipped, `15` deselected); final
  published [workflow run
  31265705618](https://github.com/slawa19/GEOv0/actions/runs/31265705618) on exact
  accepted HEAD passed required local-equivalent, PostgreSQL, production-like
  container/schema, active Chromium and Simulator super-smoke jobs. Required
  counts were backend `775 passed`, Admin `113 passed`, Simulator `668 passed`;
  the PostgreSQL matrix/full tier passed `3`/`11` tests. The overall workflow is
  not “CI green”: Admin retained its prior `2 failed / 2 passed` graph E2E set,
  and Simulator returned to its exact Phase 2 `6 failed / 13 passed` visual E2E
  baseline after three stale selected-contract mocks were corrected. Claude Code
  `2.1.226` produced two accepted read-only reviews with complete exit-`0` JSON
  and resolved `claude-opus-5`; reproduced findings were remediated and the final
  internal adversarial reviews were CLEAN. Invalid legacy Equivalent codes remain
  manual cleanup, `AdminAuditLogItem.timestamp` remains Phase 4, and replay
  ordering remains Phase 5.
- **2026-08-08:** Phase 2 completed at exact product commit
  `7d0a8a9ca48cec34cc62e0965cdd6d28825370de`; Phase 3 was not started. The
  bounded range from base `0635a651f0ae6f970d82d7d71b7a18071069262d`
  produced the compact owner/effect and actor maps in
  `phase2-owner-map.md`, assigned every frozen path `FIX` or `KEEP`, and left the
  conditional REN-012A adapter `NO TRIGGER`. Confirmed changes stayed on existing
  payment, clearing, trustline, Admin, Integrity and Simulator seams; no new
  repository/UoW framework or broad module rewrite was introduced. Published
  [workflow run 31256289008](https://github.com/slawa19/GEOv0/actions/runs/31256289008)
  executed on that exact product SHA. Its guarded disposable PostgreSQL Phase 2
  matrix passed all three mandatory cases (`3 passed`), while the conditional
  Admin lost-update row remained `NOT TRIGGERED`; the complete registered
  PostgreSQL tier passed `11` tests with `105` deselected. Required
  local-equivalent gates passed with backend `735 passed`, `2 skipped`, `15
  deselected`, Admin `76 passed` plus build, and Simulator `637 passed` plus
  build. The overall workflow conclusion remains `failure`, not “CI green”:
  scheduled Admin and Simulator visual E2E retain known failures assigned to
  later frozen phases. Local final backend validation separately passed `734`
  tests with `3` skipped and `15` deselected. Final internal adversarial findings
  were fixed in `8936031`, `8a1601f`, `c9a34cc`, and `7d0a8a9`, then
  independently re-reviewed with no remaining P1/P2. Claude Code `2.1.226`
  reviewed the full `0635a651..7d0a8a9` range from a clean credential-free
  standalone clone using `/code-review high`, `--model opus --effort high` and
  read-only tools. Exit was `0`, JSON complete, `is_error=false`, and
  `modelUsage` resolved `claude-opus-5`. Its five findings were manually checked;
  none remained a P1/P2 after verifying staged rollback ownership, the approved
  clearing taxonomy and sanitization, nested viz fallback, and multi-layer error
  logging.

- **2026-08-08:** Phase 1 completed at merged main commit
  `31e887fc904ef8060b0c1c9f233957b235ee1aeb`; Phase 2 was not started. The
  owner authorized publication, and PRs
  [#1](https://github.com/slawa19/GEOv0/pull/1),
  [#2](https://github.com/slawa19/GEOv0/pull/2), and
  [#3](https://github.com/slawa19/GEOv0/pull/3) preserved the bootstrap, migration
  caller, and test-harness remediations as separate mergeable slices. The final
  [workflow_dispatch run](https://github.com/slawa19/GEOv0/actions/runs/31246985920)
  executed on that exact SHA. Required local-equivalent gates passed with backend
  `690 passed`, `2 skipped`, `14 deselected`; Admin lint retained `0` errors and
  its registered warnings, `76` unit tests and build passed; Simulator lint,
  typecheck, `637` unit tests and build passed. The disposable PostgreSQL tier
  passed all `10` selected tests (`86` deselected). The production-like image
  passed empty-schema and `016→head` migration to
  `017_add_owner_to_simulator_runs`, ORM/index/nullability checks, readiness,
  repeat startup and three graceful exits with code `0`; short Admin/Simulator
  Chromium smoke and the three-test Simulator super-smoke also passed. The run's
  overall conclusion remains `failure`, not “CI green”: scheduled/manual Admin
  E2E reported `2 failed, 2 passed`, and Simulator E2E reported `6 failed, 13
  passed`. Their dev-hook, hidden-select, stale-real-backend and fixture-sync
  findings remain visible and are deferred to the already frozen Phase 4/5/6
  owners; Phase 1 did not weaken them or change product behavior.
- **2026-08-08:** Claude Code `2.1.224` reviewed the finished Phase 1 range
  `8f27169..6b8832b` from a clean credential-free standalone clone using
  `/code-review high`, `--model opus --effort high`, read-only tools and complete
  JSON. Exit was `0`, `is_error=false`, and `modelUsage` resolved
  `claude-opus-5`. Five reproduced workflow/runtime findings were fixed in
  `115d045`. The single remediation review of `6b8832b..115d045` used the same
  constraints and resolved model; its three suggestions were manually disposed
  as an intentional test-debt exclusion, a dynamic-head requirement, and a
  below-P2 future Playwright-version drift risk. Later published-run portability
  and test-harness corrections received independent internal adversarial review;
  no further Claude loop was opened.
- **2026-08-07:** Phase 1 local gate work was accepted in commits
  `67d02c5c3e354e561392215a9652341d95aa97a9` (local gates) and
  `889f6f945687a733cdb4e64cfa4f69ef0c5b1644` (published-job definitions only).
  The canonical local command `.\scripts\verify_local.ps1 -TaskSlug
  orchestrator_phase1_acceptance` exited `0`: backend reported `687 passed`, `3
  skipped`, `14 deselected`; Admin reported lint with `0` errors and `133`
  registered baseline warnings, `76` unit tests and a passing build; Simulator
  lint/typecheck, `637` unit tests and build passed. Parallel canonical runs with
  task slugs `orchestrator_phase1_iso_final_one` and
  `orchestrator_phase1_iso_final_two` each reported `11 passed` and used distinct
  DB, basetemp and artifact roots. Independent internal adversarial
  review found no P1/P2 in the accepted Phase 1 diff. npm audit diagnostics still
  report the pre-existing direct dev/test dependency baseline of `2` critical and
  `4` high findings; none was introduced by the three added lint dependencies,
  and no audit-fix expansion was accepted. Phase 1 remains open: Docker,
  disposable PostgreSQL, actual Chromium and a published `workflow_dispatch`
  URL/log are absent. Push/dispatch authorization has not been granted, and this
  entry does not claim CI green.
- **2026-08-07:** The repository owner explicitly approved the frozen renovation
  plan at exact commit `8f271693e7b763856d86fb3c2f579a56938d6fcb` and
  authorized the transition from Phase 0 to Phase 1. Phase 1 is now the current
  phase; its exit criteria remain open, and no later phase is authorized. This
  plan approval does not authorize a Git push or a `workflow_dispatch`; publishing
  requires a separate recorded owner authorization naming the remote, branch and
  trigger.
- **2026-08-07:** The single allowed Claude remediation review checked exact
  plan-only delta `fec834f..c0611e7` in a second credential-free clone. Claude Code
  `2.1.224` exited `0` with complete JSON and resolved `claude-opus-5` at high
  effort. It confirmed all eight original findings resolved, found no new P1 or
  overengineering, and identified one residual P2 in the dependency diagram:
  parent `REN-010` obscured the Phase 2/4 `REN-010A/B` split. The diagram and the
  adjacent stale REN-015 evidence enumeration were corrected without a third
  external cycle. The plan is ready for owner review; implementation remains
  paused until approval is recorded with the exact plan commit SHA.
- **2026-08-07:** Claude Code `2.1.224` reviewed the exact plan-only range
  `69acb2a..fec834f` from a credential-free standalone clone. The read-only run
  exited `0` with complete JSON and resolved `claude-opus-5` at high effort. Its
  two P1 and six P2 findings were manually checked. The plan now names the missing
  published container-smoke/016-upgrade path, requires separate branch/CI
  publication authorization, accepts matching published jobs as PostgreSQL/
  Chromium evidence, resolves REN-009/010 order, assigns runtime-artifact cleanup
  to REN-016, removes duplicate isolation ownership, uses exact owning sub-slices
  and records owner approval by commit SHA. One below-P2 selector suggestion did
  not expand the frozen program. A single plan fix-delta review remains before
  owner presentation.
- **2026-08-07:** The remaining program was recalibrated to the documented GEOv0
  MVP (approximately 10–500 participants, one supported community-hub topology).
  The plan now freezes seven post-approval phases, makes product code changes wait
  for owner approval, requires evidence before refactoring, and explicitly rejects
  HA/bank-grade, framework, LOC-driven, full-WCAG/browser and exhaustive cleanup
  scope. An independent read-only reviewer found four P1/P2 plan defects: required
  gates could close as `UNVERIFIED`, Integrity repair was absent from the owner
  map, parent tasks lacked unique owning sub-slices, and Claude review triggers
  were ambiguous. All four were corrected and the remediation re-review found no
  unresolved P1/P2. Claude plan review and owner approval remain pending.
- **2026-08-07:** Initial PLANNED specification created from orchestrated read-only audits. Recorded confirmed P1/P2 evidence, decision constraints, anti-regression requirements and completion criteria. No target architecture or implementation solution is approved by this entry.
- **2026-08-07:** REN-003/REN-004 implementation began in the working tree. Added task-isolated validation, fail-closed test-DB naming, explicit required versus diagnostic gate semantics, scheduled PostgreSQL/super-smoke/E2E tiers, and proven cleanup. No CI-green claim was made by this intermediate entry.
- **2026-08-07:** Foundational implementation and adversarial-review wave completed locally. The canonical default backend tier passed (`510 passed`, `5 skipped`, `4` expensive deselected); Admin lint/unit/build and Simulator typecheck/unit/build passed (`637` Simulator unit tests). REN-004 and REN-007 reached `DONE`; REN-003, REN-006, REN-008, REN-011 and REN-013 remain `IN PROGRESS` for the explicitly recorded GitHub Actions, disposable PostgreSQL, container-smoke or Playwright evidence. This entry does not claim repository-wide renovation completion.
- **2026-08-07:** Deployment/config and semantic OpenAPI slices entered
  `IN PROGRESS` after implementation, canonical targeted gates and independent
  adversarial review; Docker runtime and published CI evidence remain absent. A
  first REN-010 simulator UoW slice now stages payment side effects until an
  observed outer commit/rollback outcome and covers timeout, cancellation,
  ordering, cache and metric boundaries. This is not completion of the wider
  payment/clearing/admin UoW or PostgreSQL-concurrency scope; process-crash event
  delivery remains explicitly best-effort rather than introducing an unjustified
  outbox.
- **2026-08-07:** The first external Claude Code review workflow was calibrated
  on the frozen `710483f..e8d29e0` product range from a credential-free standalone
  clone. Built-in `/code-review high` completed with exit `0` using resolved
  `claude-opus-5` at max effort. Four findings were independently reproduced and
  fixed through owning agents; one CSRF diagnostic finding was classified P3 and
  did not expand the wave. Remediation added terminal cancellation-safe UoW
  resolution, coherent idempotent simulator observations, atomic audited Admin
  config publication and rebuild-stable graph selection. External-review failure
  semantics, bounded admission and one-pass remediation rules now live in
  `AGENTS.md` and `docs/codex-orchestrator-rule.md`.
- **2026-08-07:** The bounded remediation review completed on the frozen
  `f839311..f068e72` range in a credential-free standalone clone with Claude Code
  `2.1.224`, exit `0`, complete JSON (`is_error=false`) and resolved
  `claude-opus-5` at high effort. Both findings were manually reproduced. A
  cancelled child commit was accepted as a P2 ambiguous outcome and fixed in
  `d05c27a`; independent adversarial review found no remaining P1/P2 in that
  fix. The durable-audit/runtime-publication cancellation window was accepted as
  P2 and mapped to REN-010's already recorded crash/ambiguous-commit residual;
  it did not justify a new persistence framework or expand this bounded slice.
- **2026-08-07:** A second high-risk payment batch was reviewed from a
  credential-free standalone clone over frozen `f45b8bb..d35bc02`. Claude Code
  `2.1.224` completed with exit `0`, complete JSON (`is_error=false`) and resolved
  `claude-opus-5` at high effort. The error-taxonomy/redaction change was found
  sound. Manual triage accepted the prepare-versus-commit lock gap as P1 and
  fixed the full same-transaction prepare/commit/abort protocol in `a334c46`;
  internal adversarial review also preserved malformed-lock recovery and removed
  false-green PostgreSQL barriers. Simulator-held transaction-lock contention
  was accepted as P2 residual: the API wait is already bounded, while unlocking
  staged work or moving the tick commit boundary was not justified in this
  remediation. PostgreSQL test design is present but runtime execution remains
  unverified locally.
- **2026-08-07:** REN-003/REN-013 replaced the PostgreSQL CI file allowlist with
  a registered, suffix-owned `postgres` marker tier invoked through the canonical
  local runner. Independent adversarial review found and closed three false-green
  gaps in taxonomy discovery, workflow-command validation and local
  reproducibility. The final local backend tier passed (`683 passed`, `13
  skipped`, `4` expensive deselected); live disposable PostgreSQL and a published
  workflow run remain unverified, so neither task is marked complete.
- **2026-08-07:** The planned external Claude review of frozen
  `feef4a2..4eb31fe` did not start: the prepared standalone clone was clean and
  credential-free, but Claude Code was absent from both the Windows user PATH and
  WSL. The result is `UNVERIFIED`, not a no-findings review; temporary clone and
  bundle artifacts were removed. Internal adversarial review and local gates are
  recorded separately in REN-003/REN-013 evidence.
- **2026-08-07:** Correction to the preceding environment diagnosis: Claude Code
  `2.1.224` remained available in the newest VS Code extension and was missed
  because only PATH, standalone locations and WSL were probed. Dynamic extension
  resolution reran frozen `feef4a2..4eb31fe`; the local `/code-review` completed
  with exit `0`, complete JSON (`is_error=false`) and resolved `claude-opus-5` at
  high effort. Two findings were reproduced as P2: a skip-only SQLite false-green
  for the PostgreSQL marker tier and a non-portable PowerShell output path. Both
  were fixed with fail-closed backend validation and separator-neutral path
  construction. Three hypothetical guard/naming variants did not expose a current
  omitted test or broken gate and did not expand the slice.
- **2026-08-07:** REN-014 entered `IN PROGRESS` with a bounded authority/front-door
  slice. Root and RU Simulator indexes now distinguish observed behavior,
  accepted intent, OpenAPI, translations, target/concept material and archives;
  confirmed broken front-door links and the repository-wide translation parity
  claim were removed. All `79` changed local links resolved, and a separate
  adversarial reviewer closed two README navigation/authority findings. Deeper
  document-body reconciliation and archive/translation classification remain.
- **2026-08-07:** The one allowed external fix-delta review used a synthetic clean
  three-file range `4eb31fe..3e4ab50`, excluding intervening docs/governance
  commits. Claude Code `2.1.224` completed with exit `0`, complete JSON and
  resolved `claude-opus-5` at high effort. It confirmed the PostgreSQL backend
  preflight, then exposed two remaining P2s: default selectors still admitted
  PostgreSQL-marked tests, and programmatic backend requirements were not
  validated before reset guidance. Both were fixed; internal review additionally
  closed the `IncludeExpensive` bypass. No third external loop was opened. The
  final backend default tier passed (`687 passed`, `3 skipped`, `14`
  PostgreSQL/expensive deselected); live PostgreSQL remains unverified.
- **2026-08-09:** Phase 6 completed only REN-013B, REN-014 and REN-016 from exact
  execution base `d6e3e094d14422e20075a70839b0b288efe51bce` to accepted product
  HEAD `90ac5ef6197c184b5818fe52826a8e97dbb5f6d5`. Mutable defaults moved below
  `.local-run/`, legacy/custom DB reset is fail-closed, one no-op test step and
  proven Admin starter remnants were removed, current docs and classifications
  were reconciled, and generated sync was no-diff. The canonical milestone passed
  backend `777`, Admin `154` and Simulator `701` tests plus required builds.
  Independent adversarial review and Claude Code `2.1.226`/resolved
  `claude-opus-5` high-effort reviews have no remaining P1/P2. Exact ranges,
  commands and residuals are recorded in `phase6-cleanup-map.md`. Phase 7 was not
  started and no branch was published.
