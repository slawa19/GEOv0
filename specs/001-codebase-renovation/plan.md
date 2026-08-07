# GEOv0 Codebase Renovation Plan

- **Date:** 2026-08-07
- **Specification:** `specs/001-codebase-renovation/spec.md`
- **Executable backlog:** `specs/001-codebase-renovation/tasks.md`
- **State:** review and implementation plan; no wave is complete until its exit evidence is recorded.

## Operating model

The work proceeds top-down and in reversible waves. Review findings are not implementation decisions. At the start of each wave, the owner records:

- scope, named owner and independent reviewer;
- baseline commit and environment/tool versions;
- evidence links and hypotheses being tested;
- accepted decisions/ADRs and explicitly rejected alternatives;
- gate commands, results and retained artifacts;
- rollback trigger and rollback owner.

Only one high-risk behavioral wave may be in flight at a time. Read-only audits may run in parallel; edits that touch the same invariant or contract may not.

Priority meanings:

- **P0:** active compromise/data corruption/service-loss emergency;
- **P1:** credible security, correctness, atomicity, contract, operability or release-gate failure that blocks broad refactoring;
- **P2:** architectural/test/maintenance risk to resolve or explicitly accept during renovation;
- **P3:** cleanup or polish after higher-priority risks.

No P0 was confirmed in the initial audits. New evidence can change priority.

## Review ladder: repository to code blocks

Every owner surface is reviewed through the same ladder; later levels must not override unresolved higher-level decisions.

1. **Product/runtime inventory:** supported processes, UIs, DBs, modes, actors, entrypoints and operational flows.
2. **Trust and data boundaries:** credentials, authn/authz, owner isolation, external inputs, persisted state and destructive operations.
3. **Deployment topology:** images, Compose profiles, environment names, migrations, startup/readiness/shutdown and rollback.
4. **Source-of-truth map:** code/config/schema/OpenAPI/fixtures/docs/generated/translated/archived precedence.
5. **Domain invariants:** accounting, trustline direction/capacity, payment terminal states, clearing, idempotency, simulator ownership and event ordering.
6. **Architecture/dependency direction:** API → application/use case → domain → infrastructure; frontend entry → transport → validation/reducer → state → rendering/UI.
7. **Module ownership:** responsibilities, state owner, transaction owner, lifecycle owner, public API, consumers and hidden globals.
8. **Inter-module logic:** call sequences, error mapping, retries, locks/leases, cache invalidation, event replay, async cancellation and cleanup.
9. **Function/code blocks:** preconditions, postconditions, branches, numeric precision, mutations, exception scope, resource cleanup and complexity.
10. **Tests and operations:** prove behavior at the right boundary, failure modes, dialect/platform matrix, observability and runbook accuracy.

For each reviewed module, produce a compact record: `purpose → callers → dependencies → state/side effects → invariants → risks → tests → decision`.

## Wave 0 — Security and reproducible baseline

### Goals

- Freeze a trustworthy baseline before cleanup/refactoring.
- Close or explicitly contain P1 production-configuration ambiguity.
- Confirm the credential audit without exposing values.
- Establish supported runtime/tool/dialect/platform matrix.

### Evidence to collect

- `app/config.py:44-46`, `app/config.py:151-221`; `docker-compose.yml:37-45`; `docker-compose.dev.yml:12-16`.
- Auth/owner/CSRF path: `app/api/deps.py:232-321` and simulator cookie creation/config.
- Current branch/commit, dirty-tree inventory, Python/Node/npm versions, dependency locks, test counts, fixture hashes and current pass/fail/skip/flaky results.
- Secret scan of tracked tree and history; retain only redacted summary. Initial audit result: no tracked/history credential requiring removal; owner-directed embedded origin credential remains untouched.

### Candidate changes, subject to decision

- Decide and document the canonical environment variable (`ENV` or a deliberate replacement), then align Compose/examples/tests.
- Separate explicit dev and production-like profiles; make production-like startup fail on placeholder JWT/admin/session secrets and unsafe cookie-origin policy.
- Add a configuration/preflight test matrix without logging secret values.
- Record supported Python/Node/npm/PostgreSQL/SQLite/browser versions.

### Gates

- Clean-install/version capture succeeds.
- Redacted secret scan produces zero unowned findings.
- Config matrix tests cover dev, test, staging/prod-like and mistyped/missing variables.
- Authn/authz/owner-isolation/CSRF tests pass for admin, participant and anonymous actors.

### Exit criteria

- Baseline manifest is committed/linked.
- Production-like settings cannot silently inherit dev semantics.
- Local dev workflow remains available through an explicitly named profile.
- No secret is printed, rotated, committed or history-rewritten.

### Rollback

- Revert only config/profile/preflight commits; restore the pre-wave launch command.
- Never roll back by disabling non-dev secret/origin guardrails. If local startup breaks, restore a separate explicit dev profile.

## Wave 1 — Governance and executable quality gates

### Goals

- Turn repository rules into commands that work on a clean checkout and into CI enforcement.
- Define ownership, priority, flaky/quarantine and change-scope rules.

### Evidence to collect

- Absence of `.github/workflows/`.
- `scripts/verify_local.ps1:1-19` omissions.
- Admin scripts `admin-ui/package.json:9-23`; Simulator scripts/dependencies `simulator-ui/v2/package.json:6-36`; narrow ESLint config `simulator-ui/v2/eslint.config.js:1-19`.
- Duplicate pytest configuration in `pyproject.toml:9-12` and `pytest.ini:3-32`; dependency pins in requirements/package locks.

### Candidate changes, subject to decision

- Establish canonical commands for backend format/lint/static/unit/integration/contract; Admin install/lint/typecheck/unit/build/e2e; Simulator install/lint/typecheck/unit/build/e2e.
- Add missing Simulator Vue/TypeScript lint toolchain and explicit Admin typecheck command.
- Add CI jobs with cache keys based on lockfiles, plus Postgres/migration and platform-scoped visual jobs.
- Make the local verifier call the same scripts as CI.
- Add a quarantine register and prohibit unowned skips/placeholders.

### Gates

- CI is tested from a clean checkout.
- Each command fails on an intentional controlled violation.
- No gate mutates canonical fixtures or snapshots unexpectedly.
- Windows/local runner and CI use the same underlying package scripts.

### Exit criteria

- Required checks are branch-visible and documented.
- Backend, both active UIs, contracts and migrations cannot be skipped by the aggregate verifier.
- Lint/typecheck/test/build responsibilities are unambiguous.

### Rollback

- Revert individual CI/tooling jobs without weakening already-working gates.
- Preserve canonical scripts locally while diagnosing CI infrastructure failure; record temporary non-blocking status with owner/expiry rather than delete the check.

## Wave 2 — Repository hygiene and active/archive boundaries

### Goals

- Remove tracked runtime/test output and proven dead starter artifacts.
- Make active, archived, generated and canonical fixture surfaces explicit.
- Reduce duplicate runnable infrastructure only after evidence.

### Evidence to collect

- `git ls-files`, ignore rules, file sizes, imports/runtime discovery, docs links and Git history.
- Tracked `.tmp_*` and `simulator-ui/v2/test_output.txt:2-30`.
- `admin-ui/src/components/HelloWorld.vue:1-3`, `admin-ui/src/assets/vue.svg`, `admin-ui/index.html:5-7`.
- `simulator-ui/README.md:3-6`, v1 package/lock/source, and v2 legacy-reference snapshots/docs.
- Duplicate root `Dockerfile` versus `docker/Dockerfile`; active/manual-operations and archived spec copies.

### Candidate changes, subject to decision

- Untrack local outputs and strengthen ignores/checks.
- Remove proven unused starter assets and replace placeholder product metadata.
- Decide `simulator-ui/v1` disposition: keep read-only with no normal gates, archive outside runnable tree, or delete after preserving required references.
- Classify fixture sources versus synchronized copies; enforce sync rather than hand edits.
- Create a machine-readable or documented repository inventory.

### Gates

- Clean checkout/build/test after each deletion batch.
- Import/reference/docs/history search attached to deletion review.
- Fixture sync/validation produces no unexplained diff.
- No migration or reference snapshot is deleted as “garbage” without replacement evidence.

### Exit criteria

- `git status` remains clean after normal run/test/build workflows.
- Every top-level/runtime UI directory has an active/archive/generated classification.
- No tracked log, temp run state, local DB, report or test output remains.

### Rollback

- Revert deletion batch or restore file from Git.
- If a hidden consumer appears, restore first, then add coverage/discovery evidence before reconsidering removal.

## Wave 3 — Deployment, startup and schema/migration consistency

### Goals

- Make one production image path and explicit dev override behavior.
- Align ORM, migrations and supported DB dialect behavior.
- Make startup, migration, readiness and rollback deterministic.

### Evidence to collect

- Base Compose builds `docker/Dockerfile` (`docker-compose.yml:30-34`); dev override builds root `Dockerfile` (`docker-compose.dev.yml:5-11`).
- `docker/docker-entrypoint.sh:59-66` migrates then launches; root `Dockerfile:48-53` has separate health/CMD behavior.
- ORM/migration owner mismatch: `app/db/models/simulator_storage.py:19-21,42-53` versus `migrations/versions/017_add_owner_to_simulator_runs.py:39-49`.
- SQLite runtime schema alteration in `app/main.py:27-69` versus Alembic policy.
- Empty DB, current fixtures, representative previous revision and actual PostgreSQL metadata introspection.

### Candidate changes, subject to decision

- Choose one canonical production Dockerfile/entrypoint and an explicit dev target/override.
- Decide whether migrations run in entrypoint, one-shot job or operator step; document race/rollback semantics.
- Reconcile ORM nullability/index definitions with migrations and legacy-row behavior.
- Decide supported SQLite migration policy; remove ad hoc runtime DDL only after replacement exists.
- Add readiness distinct from liveness and include required background/migration state.

### Gates

- Build and boot production-like image from empty PostgreSQL.
- Upgrade from supported previous revision; schema diff ORM↔DB is empty or explicitly allow-listed.
- Repeated startup/migration is idempotent; concurrent replicas do not race migrations.
- Dev override uses expected code mount/reload and cannot be mistaken for production.

### Exit criteria

- One documented image/entrypoint truth with explicit dev variation.
- Schema ownership/nullability/indexes agree for supported dialects.
- Migration and rollback/forward-fix procedures are rehearsed and linked.

### Rollback

- Image/config changes revert to prior digest/Compose files.
- Schema changes use the pre-approved downgrade or forward-fix; never edit an already-applied migration in place.

## Wave 4 — API/event contracts and observability

### Goals

- Establish one enforceable contract chain from backend schema to frontend consumer.
- Make required background jobs, overload/drop and replay failures visible.
- Standardize error taxonomy without exposing raw internals.

### Evidence to collect

- OpenAPI test limitation `tests/contract/test_openapi_contract.py:42-86` and canonical/generated specs.
- Pydantic schemas, API response models/error handlers, `api/openapi.yaml`, Admin Zod schemas and Simulator normalizer/types.
- Direct HTTP cast `simulator-ui/v2/src/api/http.ts:44-68`; unknown event cast `normalizeSimulatorEvent.ts:367-369`; catch-all union `simulatorTypes.ts:308-315`.
- Silent background tasks `app/main.py:109-120,170-182` and event bus `app/utils/event_bus.py:43-53`.
- Raw prepare exception mapping `app/core/payments/service.py:464-496`.

### Candidate changes, subject to decision

- Choose canonical OpenAPI generation/verification direction; compare operations, parameters, bodies, responses, errors and security schemes.
- Define/version SSE event envelopes and unknown-event policy; add captured producer/consumer contract fixtures.
- Introduce typed error categories/codes and sanitize internal details.
- Move QueueFull handling into the event-loop callback or an async queue API; add drop/backpressure metrics.
- Supervise required background tasks; expose started/failed/restarting/last-success state via logs, metrics and readiness as appropriate.

### Gates

- Intentional payload/schema/security drift breaks contract tests.
- Backend fixture → frontend decoder parity tests cover every supported event.
- Unknown/malformed event behavior is deterministic and observable.
- Fault injection proves required job failures and queue overload emit actionable signals without leaking secrets.

### Exit criteria

- Contract source and generation direction are documented.
- No supported frontend ingress relies on an unchecked broad cast.
- Required background subsystem absence cannot be silent.
- Client-facing errors preserve stable code/status while internal cause remains observable server-side.

### Rollback

- Keep compatibility adapters/old event version during the accepted transition window.
- Revert strict rejection to observe/report mode if valid existing traffic is discovered, without removing diagnostics.

## Wave 5 — Backend UoW, concurrency and simulator architecture

### Goals

- Define and prove transaction ownership, atomicity, idempotency, lock/lease and retry semantics.
- Decompose backend god modules around accepted use-case/domain/infrastructure boundaries.
- Preserve accounting, clearing and simulator event invariants.

### Evidence to collect

- Commit/rollback/flush/begin_nested call graph across API, services and engines; focus on `app/core/payments/service.py:401-560`, `app/core/payments/engine.py`, clearing and simulator actions.
- PostgreSQL row locks/serializable retries, optimistic `version`, distributed/in-process locks and timeout/lease cleanup.
- Simulator serialization points, runner/action/tick/SSE interplay, owner isolation and shutdown recovery.
- Large-module responsibility maps for `app/api/v1/simulator.py`, `admin.py`, payment engine/service, clearing service and simulator runtime/executors.
- Existing concurrent payment/clearing, idempotency, invariant, recovery and owner-isolation tests.

### Candidate changes, subject to decision

- Select and record UoW ownership (for example request/use-case boundary) only after call-graph and failure analysis; services/engines should not retain ambiguous commit policy.
- Separate route parsing/auth, application commands/queries, domain transitions, persistence adapters and event publication incrementally.
- Make retryable conflict, timeout, cancellation and terminal outcomes typed.
- Define lock scope, ordering, lease renewal/expiry and multi-process behavior; retain serialization where it protects invariants unless tests justify finer concurrency.
- Extract pure simulator reducers/planners from runtime orchestration where behavior can be characterized.

### Gates

- Postgres contention tests: same-edge payments, clearing versus payment, duplicate idempotency keys, lock expiry, worker/process concurrency and injected commit acknowledgement loss.
- Accounting/integrity invariant suite before and after each slice.
- Terminal transaction state cannot regress; committed operation is not aborted on ambiguous response.
- Simulator owner isolation, replay order and graceful recovery remain intact.
- Module dependency checks prevent API/infrastructure imports into pure domain layers chosen by ADR.

### Exit criteria

- One transaction owner per use case; commit/rollback semantics are no longer a boolean propagated through layers.
- Concurrency contract is documented and proven for supported PostgreSQL deployment.
- High-risk modules have narrower responsibilities and stable facades, with no behavior loss.
- Remaining serialization/lease limitations are explicit, measured and owned.

### Rollback

- One use-case seam per commit; revert to prior facade/transaction path.
- Keep compatibility facade until all callers and failure tests pass.
- Any invariant/concurrency regression triggers immediate wave rollback, not a test relaxation.

## Wave 6 — Admin UI async state and API boundaries

### Goals

- Eliminate stale-response, duplicate-load and post-unmount side effects.
- Make Admin real/mock API parity and runtime validation explicit.
- Reduce graph/page responsibility mixing without changing supported workflows.

### Evidence to collect

- Participant and Trustline load/watch flows (`ParticipantsPage.vue:100-125,192-223`; `TrustlinesPage.vue:130-156,172-203`).
- Graph stale application path (`useGraphData.ts:200-219`; `useGraphPageWatchers.ts:74-85`) and selection clearing-cycle fetch (`useGraphPageWatchers.ts:100-118`).
- Debounce/throttle lifecycle (`admin-ui/src/utils/debounce.ts:1-22`; `admin-ui/src/utils/throttle.ts:5-23`) and all consumers.
- Real/mock API schemas/casts (`admin-ui/src/api/realApi.ts:33-118,562-618`; `admin-ui/src/api/mockApi.ts:322`).
- Hotspots: Graph page, visualization, analytics drawer, mock/real API and route-query synchronization.

### Candidate changes, subject to decision

- Adopt a consistent latest-request/AbortController ownership primitive and component cleanup; decide loading semantics for superseded requests.
- Collapse watcher chains into explicit route-state → request-key → load flow where evidence supports it.
- Validate all network/fixture payloads at ingress; use the same domain contract for mock and real clients.
- Separate Cytoscape adapter/rendering from graph data/query/analytics state while retaining one lifecycle owner.
- Add keyboard-accessible graph navigation/selection and accessible status/error semantics.

### Gates

- Deterministic reverse-resolution tests prove stale data/error/finally cannot win.
- Navigation/unmount tests prove no delayed router/fetch/render side effects.
- Mock/real contract parity tests use shared cases.
- Admin lint/typecheck/unit/build pass; affected page/graph Playwright flows pass in mock and selected real-contract harness.

### Exit criteria

- Every async screen has explicit request and cleanup ownership.
- URL, filters, pagination, data and loading/error state cannot contradict after rapid navigation.
- Broad API casts are removed or confined behind validated boundaries.
- Critical graph operations have keyboard-accessible equivalents.

### Rollback

- Keep old page/API facade behind focused compatibility seam until parity tests pass.
- Revert one screen/graph slice independently; do not restore stale-response behavior as a global fallback.

## Wave 7 — Simulator UI transport, reducer, render and accessibility

### Goals

- Separate REST/SSE transport, validation/replay, pure state reduction, render/FX side effects and Vue presentation.
- Reduce `useSimulatorApp`, `useSimulatorRealMode` and `SimulatorAppRoot` responsibility without breaking real/fixtures/interact modes.
- Provide an accessible equivalent for canvas interaction.

### Evidence to collect

- Facade claim/import/API surface `simulator-ui/v2/src/composables/useSimulatorApp.ts:1-10,12-71,95-330,1750-1805`.
- Root orchestration and canvas `SimulatorAppRoot.vue:1-230,1000-1035`.
- SSE parsing/loop/replay/FX/state mutation `useSimulatorRealMode.ts:504-680,853-898`.
- HTTP/event validation boundaries `src/api/http.ts:44-68`, `normalizeSimulatorEvent.ts:75-369`, `simulatorTypes.ts:308-315`.
- Rendering/layout/camera/picking/window/FSM dependency graph, timer/RAF/observer ownership and performance counters.
- Active v2 versus reference v1 evidence (`simulator-ui/README.md:3-6`).

### Candidate changes, subject to decision

- Introduce a transport client, validated decoder, replay cursor/deduplicator, pure run/snapshot reducer and effect subscribers; preserve current facade during migration.
- Move visual labels/FX scheduling out of transport callback; explicitly order reducer application, rendering wakeup and UI effects.
- Create runtime contexts with narrow interfaces: session/run, graph, render/camera, interact, windows and diagnostics.
- Define unknown/malformed event and reconnect/410/full-refresh semantics.
- Add DOM-based node/edge navigator or a reviewed canvas keyboard model with instructions/live region; mark decorative FX canvas hidden.
- Preserve design-system and overlay/window rules; treat snapshots as intentional artifacts.

### Gates

- Recorded event-sequence tests cover duplicates, replay, reconnect, stale run id, 410, topology patches, transaction/clearing patches and effect ordering.
- Pure reducer tests run without Vue/canvas/time.
- Timer/RAF/listener/ResizeObserver leak tests and idle/wakeup/performance budgets pass.
- Keyboard-only payment/trustline/clearing/inspect flows and accessibility assertions pass.
- Simulator lint/typecheck/unit/build and scoped Playwright visual/real-mode tests pass.

### Exit criteria

- Transport does not directly own rendering/FX or presentation state.
- State transitions/replay are deterministic and independently testable.
- Facade/root have bounded, documented responsibilities; no decomposition is accepted solely for lower LOC.
- Critical canvas workflows are keyboard accessible and status/errors are announced.

### Rollback

- Preserve old facade and switch one pipeline at a time through compatibility adapters.
- Revert reducer/transport/render slice independently; retain captured sequences for diagnosis.
- Do not update snapshots to hide behavioral or timing regressions.

## Wave 8 — Test suite renovation

### Goals

- Remove false confidence, isolate mutable resources and align tests with risk/architecture.
- Retain valuable characterization while reducing monolithic and source-text-only tests.

### Evidence to collect

- Placeholder tests `tests/test_e2e_example.py:23-100` and placeholder fixtures `tests/conftest.py:226-260`.
- Fixed DB/basetemp paths `tests/conftest.py:26-29`, `pytest.ini:20-24`.
- OpenAPI coverage limitation, source-text wiring tests, skip/xfail/flaky inventory and test duration/failure concentration.
- Admin test inventory and missing destructive/config/auth/real-mode flows.
- Simulator giant tests and Windows-only snapshots.

### Candidate changes, subject to decision

- Replace placeholders with real assertions or delete them after confirming no framework value.
- Allocate per-worker temp DB/basetemp/artifact roots; define safe Postgres test DB guardrails.
- Reclassify tests by unit/contract/integration/e2e/visual/performance and enforce markers/discovery.
- Split giant test files by behavior; replace source-text checks with import/behavior/architecture checks.
- Define platform policy for visual snapshots and intentional update review.
- Add failure-injection and concurrency cases derived from waves 3–7.

### Gates

- Test collection contains no pass-only/placeholder scenario counted as success.
- Supported parallel run has no shared-file/schema collisions.
- Controlled mutation of critical invariant causes a test failure.
- Flaky/quarantined tests have owner, issue and expiry.
- Test runtime and artifacts remain within recorded budgets.

### Exit criteria

- Each critical invariant maps to at least one meaningful test at the correct layer.
- Green status is not inflated by placeholders or platform-inapplicable snapshots.
- Test resources are isolated and cleanup is deterministic.

### Rollback

- Preserve original characterization tests until replacements demonstrate equivalent or stronger signal.
- Revert test infrastructure separately from product changes; never roll back by deleting failing behavioral assertions.

## Wave 9 — Documentation and source-of-truth consolidation

### Goals

- Make documentation describe the verified implementation and accepted decisions.
- Establish precedence for config, API, schema, active specs, translations and archives.

### Evidence to collect

- Root README, architecture, config reference, API YAML/generated FastAPI schema, deployment/testing runbooks, UI READMEs and development standards.
- Conflicting claims such as `docs/ru/development-standards.md:644-646` pointing Vitest config to `vite.config.ts` although `vitest.config.ts` is used.
- Active/archive/manual-operations duplicates and translation divergence.
- Commands executed by CI/local verifier after prior waves.

### Candidate changes, subject to decision

- Declare canonical sources and generation/update workflows.
- Update architecture maps, UoW/concurrency/event contracts, deployment profiles, migration/readiness, test matrix and UI module maps.
- Mark archives as non-normative and link successor decisions; consolidate duplicate active specs.
- Update canonical language first, then translations with explicit synchronization status.
- Add automated link/command/config-key/schema-reference checks where durable.

### Gates

- Every documented command runs from a clean checkout in its stated shell/platform or is explicitly platform-scoped.
- Link and source-of-truth checks pass.
- Config/API/schema examples validate against executable definitions.
- Independent reviewer traces one backend and one UI flow using docs only.

### Exit criteria

- No competing normative claim remains unexplained.
- Archives/translations cannot be mistaken for current authority.
- Documentation points to the accepted code/contracts/gates, not aspirational completion labels.

### Rollback

- Revert inaccurate doc batch while retaining implementation evidence.
- Do not roll back code to match stale documentation; open a discrepancy with owner and deadline.

## Wave 10 — Final adversarial review and closure

### Goals

- Try to falsify success claims across security, data integrity, contracts, concurrency, UI lifecycle, accessibility, deployment and recovery.
- Produce closure evidence and a bounded residual-risk register.

### Evidence to collect

- All wave artifacts, ADRs, gate runs, diffs, migration rehearsals, test/flaky reports, performance measurements and documentation checks.
- Fresh repository inventory and P0–P3 finding register.
- Clean-clone production-like and developer bootstrap logs.

### Adversarial scenarios

- Start with missing/mistyped secrets/environment; attempt cross-owner simulator access and cookie-origin violations.
- Upgrade representative old DB, start two app replicas, fail one during migration/job startup, and verify readiness/recovery.
- Inject concurrent payment/clearing/idempotency/commit-ack failures and verify invariants/terminal states.
- Drift OpenAPI/SSE payload and confirm backend/frontend gates detect it.
- Overflow event queues, terminate/restart SSE, replay duplicates/out-of-window cursors and verify state/effects/metrics.
- Rapidly change Admin routes/filters, unmount during requests and resolve responses out of order.
- Complete critical UI flows keyboard-only and with reduced motion/accessibility tooling.
- Run tests in parallel and verify no shared DB/temp/artifact collision.
- Run normal developer workflows and confirm no tracked/generated dirt appears.

### Candidate changes

- Only fixes directly justified by adversarial findings; new architecture/features return to the relevant wave and decision process.
- Record accepted residual P2/P3 risks with owner, rationale and target date.

### Gates

- Full canonical CI/local matrix on the closure commit.
- Independent security, backend/concurrency, frontend/accessibility and repository/docs reviewers sign their surfaces.
- Zero unresolved P0/P1.

### Exit criteria

- All success criteria in `spec.md` have linked evidence.
- No unresolved P0/P1; every accepted P2 has explicit ownership and disposition.
- Final status change is reviewed separately and cites evidence; the status label alone remains non-authoritative.

### Rollback

- Any discovered P0/P1 reopens the owning wave and blocks closure.
- Roll back the smallest responsible wave; use rehearsed DB forward-fix/downgrade and compatibility adapters where applicable.

## Evidence log template

Append or link one record per wave without rewriting earlier observations:

```text
Wave:
Owner / reviewer:
Baseline commit:
Scope:
Confirmed findings:
Decisions / ADRs:
Changes:
Commands and environments:
Results / artifacts:
Deferred risks with owner/date:
Rollback rehearsal/result:
Exit review:
```

## Dependency order

```text
security/baseline
  → governance/gates
  → repository boundaries
  → deployment/schema
  → contracts/observability
  → backend UoW/concurrency/simulator
  → Admin async/API
  → Simulator transport/reducer/render/accessibility
  → test-suite consolidation
  → documentation consolidation
  → adversarial closure
```

Frontend waves may begin read-only characterization while backend work runs, but frontend contract edits wait for Wave 4 decisions. Documentation inventory may run early, but normative rewrites wait for verified implementation. Repository deletions that affect deployment, fixtures, tests or reference snapshots wait for the owning wave's evidence.
