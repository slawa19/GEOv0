# GEOv0 Codebase Renovation Specification

- **Date:** 2026-08-07
- **Status:** PLANNED
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
- Do not rotate credentials, rewrite Git history, deploy, or mutate production/external systems under this specification.

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
- Exercise both SQLite and PostgreSQL for persistence/concurrency claims; SQLite success is not evidence of PostgreSQL schema/locking correctness and vice versa.
- Verify migrations from empty DB and supported previous revision; verify downgrade only where the project promises downgrade support.
- Verify OpenAPI payloads, errors and security declarations, not only path/method inventory.
- Preserve idempotency, accounting invariants, owner isolation, event ordering/replay and COMMITTED-terminal semantics.
- For async UI work, only the current request owner may apply data, error and loading state; timers/listeners/RAF/observers must be disposed.
- Preserve a keyboard-accessible route through every supported critical UI flow.
- Preserve fixture determinism and use synchronization scripts; do not hand-edit generated public fixture copies.
- Maintain an explicit flaky/quarantine register. A quarantine needs owner, reason, issue and expiry; skipped/placeholder tests do not count as coverage.
- Treat secret scan, dependency audit and production-config preflight as gates; do not print sensitive values in logs/artifacts.
- Roll back by reverting the wave, restoring compatibility adapters and re-running the pre-wave gates. Database changes require a tested forward-fix or downgrade plan before execution.

## Success criteria

The renovation is complete only when all of the following have evidence linked from the plan:

1. Active, legacy, generated and disposable surfaces are catalogued; every deletion has reachability/history evidence.
2. Canonical local and CI commands exist for backend, Admin UI and Simulator UI; clean checkouts reproduce them with pinned toolchains.
3. CI enforces formatting/lint, type/static checks, unit, contract, migration, build and scoped e2e gates. No placeholder/pass-only test is counted.
4. Production-like config rejects insecure defaults and unknown/mistyped deployment variables; base and dev Compose semantics are explicit and tested.
5. ORM metadata, migrations and runtime expectations agree for every supported dialect, including simulator owner nullability/indexes.
6. OpenAPI and SSE contracts validate schemas, errors and security; backend/frontend fixtures and consumers have contract parity tests.
7. Required background jobs expose startup/readiness/runtime failure signals; intentional drops and retries are observable.
8. Payment/clearing/simulator transaction boundaries and concurrency semantics are documented and proven by contention, idempotency and failure-injection tests.
9. API route modules, domain services and simulator orchestration have explicit dependency direction and materially smaller responsibility sets; no target is accepted on LOC alone.
10. Admin UI stale-response races and delayed-work cleanup are covered by deterministic tests and fixed without URL/filter regressions.
11. Simulator transport, event validation/reduction, rendering/FX and UI orchestration have testable boundaries; unknown events and reconnect/replay semantics are explicit.
12. Critical Admin graph and Simulator interaction flows are keyboard usable and have accessible status/error/dialog semantics.
13. Test DBs/temp paths are worker-isolated; supported parallel runs do not share mutable fixed files.
14. Repository contains no tracked runtime/test output or unexplained starter/duplicate artifact; legacy v1 has an explicit disposition.
15. Canonical architecture, configuration, API, testing, deployment and UI documentation matches the verified result; archives/translations are clearly subordinate.
16. An independent adversarial review finds no unresolved P1, and each accepted P2 has an owner and dated follow-up or documented risk acceptance.

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
