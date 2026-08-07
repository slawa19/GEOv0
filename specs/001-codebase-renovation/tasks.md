# Codebase Renovation — executable backlog

Date: 2026-08-07  
Owner surface: whole repository  
Specification: `specs/001-codebase-renovation/spec.md`  
Plan: `specs/001-codebase-renovation/plan.md`  
Status vocabulary: `DONE`, `READY`, `PLANNED`, `BLOCKED`, `IN PROGRESS`, `ACCEPTED-NOT-DOING`.

This backlog turns the comprehensive review into a sequence of independently
reviewable slices. A task is not complete because code moved or a metric became
green: its acceptance criteria and named gates must demonstrate the intended
effect on every reachable path in scope.

## Value filter and execution rules

Apply this filter before starting a task and again before accepting review
findings:

1. Prefer changes with broad effect relative to implementation and review cost:
   security boundaries, data integrity, transaction ownership, stable contracts,
   common failure paths, and developer feedback loops.
2. Do not expand a slice for rare edge cases without evidence of material user,
   operator, integrity, or maintenance impact.
3. Do not introduce frameworks, abstraction layers, services, or generalized
   infrastructure merely because the current code is old or large. Extract only
   a demonstrated responsibility with more than one meaningful consumer or a
   clearly protected boundary.
4. Do not mechanically align code to documents or documents to code. Determine
   whether each document describes current behavior, a target, or history; use
   code, tests, migrations, runtime evidence, and Git history to decide which side
   is authoritative for the specific contract.
5. Cleanup requires proof: references, Git history, replacement coverage, and
   targeted tests. A name containing `tmp`, `legacy`, `archive`, or `example` is
   an inventory signal, not deletion authority.
6. Each implementation slice gets a fresh targeted test, a full applicable gate,
   and an independent review of call-sites and sibling paths. Keep unrelated
   formatting and opportunistic refactors out of the diff.
7. If a full gate is temporarily unavailable because an earlier dependency has
   not landed, record that limitation; do not call a debug-only substitute
   equivalent.

## Canonical gate vocabulary

- `BACKEND-TARGETED`: `.\scripts\verify_local.ps1 -TaskSlug <unique-task> -BackendOnly -BackendSelector <selectors>`
- `BACKEND-DEFAULT`: `.\scripts\verify_local.ps1 -TaskSlug <unique-task> -BackendOnly`;
  runs all backend tests except the explicitly marked `slow`/`e2e` tiers.
- `BACKEND-EXPENSIVE`: add `-IncludeExpensive` with an explicit slow/e2e selector;
  it is not part of the default tier.
- `BACKEND-LINT-DIAGNOSTIC`: `.\.venv\Scripts\python.exe -m ruff check app migrations --no-cache`;
  this is known-red and non-blocking until a baseline/ratchet is accepted.
- `OPENAPI`: `.\scripts\verify_local.ps1 -TaskSlug <unique-task> -BackendOnly -BackendSelector tests/contract/test_openapi_contract.py`
- `ALEMBIC-HEADS`: `.\.venv\Scripts\python.exe scripts/check_alembic_heads.py`
- `ADMIN-UNIT`: `npm --prefix admin-ui run test`
- `ADMIN-BUILD`: `npm --prefix admin-ui run build`
- `SIM-UNIT`: `npm --prefix simulator-ui/v2 run test:unit`
- `REPO-FULL`: `.\scripts\verify_local.ps1 -TaskSlug <unique-task>`; Ruff/Black
  remain separately reported diagnostics and are not described as enforced.
- `POSTGRES-CONCURRENCY`: the named selector against a dedicated disposable
  PostgreSQL database only; never point `TEST_DATABASE_URL` at developer data.

Commands above describe repository intent. REN-003 owns making them unambiguous
and CI-reproducible; until it lands, record the exact executable and environment
used.

---

## REN-001 — Audit token exposure in repository and Git history

- **Priority:** P1
- **Owner surface:** `.git/config`, tracked files, all reachable Git objects,
  configured `origin`
- **Status:** DONE (2026-08-07)
- **Rationale / value:** Distinguish a local remote credential from a committed or
  historical secret before any repository publication or cleanup.
- **Evidence paths:** `.git/config`; `git ls-files`; all reachable Git history;
  owner decision dated 2026-08-07.
- **Exact scope:** Inspect whether `origin` contains credentials; scan tracked
  content and reachable history without printing the credential; record counts
  and disposition.
- **Non-scope:** Credential rotation, remote mutation, rewriting history, removing
  the configured remote, or displaying the token.
- **Dependencies:** None.
- **Acceptance criteria:**
  - Audit recorded `origin credential present = true`.
  - Matches in tracked files = `0`; matches in reachable Git history = `0`.
  - No secret value is copied into logs, specs, tasks, commits, or chat output.
  - Remote remains configured by explicit owner decision on 2026-08-07.
- **Targeted gates:** Read-only tracked/history scans with redacted output.
- **Full gates:** Not applicable; no repository content changed.

## REN-002 — Establish AGENTS and specification foundation

- **Priority:** P1
- **Owner surface:** `AGENTS.md`, `specs/001-codebase-renovation/`
- **Status:** DONE (2026-08-07)
- **Rationale / value:** Give every later agent the same source-of-truth order,
  protected contracts, test tiers, transaction rules, and evidence discipline.
- **Evidence paths:** owner-supplied external reference
  `D:/www/Projects/2025/DocxAICorrector/docs/AGENTS.template.md`;
  `README.md:298-445`; `.github/copilot-instructions.md:25-41,77-168`;
  `docs/ru/development-standards.md:590-724`.
- **Exact scope:** Create a short project-specific front door; define current vs
  target docs, canonical commands, Windows/PowerShell runtime, Python 3.11 target,
  SQLite/PostgreSQL split, destructive test-DB guard, OpenAPI/migration/SSE/
  trustline contracts, expensive simulator gates, agent ownership, and this
  spec's decision/status format.
- **Non-scope:** Restating all architecture or domain documentation inside
  `AGENTS.md`; implementing product fixes; declaring historical plans current.
- **Dependencies:** REN-001.
- **Acceptance criteria:**
  - A new agent can identify entrypoints, cheap/full/Postgres-only gates, protected
    contracts, and dangerous commands without opening historical plans.
  - Rules explicitly require preserving user changes and non-overlapping agent
    write surfaces.
  - `AGENTS.md`, spec, plan, and this backlog cross-link without contradictory
    status claims.
- **Targeted gates:** Markdown/link review; fresh verification of every cited
  command and file path.
- **Full gates:** Not required for documentation-only changes.

## REN-003 — Make validation canonical and enforce it in CI

- **Priority:** P1
- **Owner surface:** `.github/workflows/`, `scripts/verify_local.ps1`,
  `pyproject.toml`, `pytest.ini`, dependency manifests
- **Status:** IN PROGRESS (2026-08-07)
- **Working-tree evidence (2026-08-07):** PowerShell AST and workflow YAML checks
  passed; fail-closed DB guard selector passed `12` tests; sequential Simulator
  unit tier passed `637` tests and production build exited `0`. The task-local
  aggregate completed the backend default tier (`510 passed`, `5 skipped`, `4`
  expensive deselected), the single-head check, Admin lint/unit/build, and
  Simulator typecheck/unit/build. Disposable PostgreSQL migration, Playwright
  jobs, and a published GitHub Actions run are not established by this evidence
  entry.
- **Rationale / value:** A workflow and aggregate runner now exist in the working
  tree, but they are not accepted until clean-checkout jobs and every required
  surface complete. Ruff/Black debt is visible diagnostic evidence, not an
  enforced or green gate.
- **Evidence paths:** `scripts/verify_local.ps1:1-18`;
  `docs/ru/development-standards.md:668-679`; `.github/`; `pyproject.toml:1-9`;
  `pytest.ini`; current ruff result recorded in the review.
- **Exact scope:** Define one local aggregate command and matching GitHub workflow;
  pin the runtime and install inputs; run backend tests, OpenAPI contract, lint,
  Admin unit/build gates, and explicitly named simulator gates; either clear the
  current Ruff/Black debt explicitly as non-blocking diagnostics. Establishing an
  enforced two-sided ratchet remains follow-up work before the specification's
  final lint-enforcement success criterion can be claimed.
- **Non-scope:** Fixing behavioral defects found by tests, upgrading all
  dependencies, adding every expensive E2E/real-mode run to each PR, or masking
  failures with broad excludes.
- **Dependencies:** REN-002.
- **Acceptance criteria:**
  - Local and CI commands select the same checks and fail loudly on invalid
    selectors, missing dependencies, or lint/test failure.
  - CI uses a clean checkout and the documented Python target.
  - PostgreSQL-only and expensive simulator runs have an explicit trigger/schedule
    rather than being silently skipped or run on every edit.
  - Ruff/Black output is visible and explicitly diagnostic; no documentation or CI
    result describes it as enforced until a separately accepted baseline/ratchet exists.
  - README and AGENTS point to the runner rather than duplicating volatile counts.
- **Targeted gates:** Run each new job/step locally where available; validate
  workflow syntax; `BACKEND-LINT-DIAGNOSTIC`; `OPENAPI`.
- **Full gates:** `REPO-FULL`; final CI run on the pushed branch.

## REN-004 — Remove proven tracked artifacts and placeholder tests safely

- **Priority:** P1
- **Owner surface:** root tracked artifacts, `scripts/`, `tests/test_e2e_example.py`,
  placeholder fixtures in `tests/conftest.py`, `.gitignore`
- **Status:** DONE (2026-08-07)
- **Working-tree evidence (2026-08-07):** post-deletion live reference scan found
  no consumer of the removed placeholders/runtime outputs. The backend default
  tier completed with `510 passed`, `5 skipped`, and `4` explicitly expensive
  tests deselected; Admin and Simulator required local gates also completed.
- **Rationale / value:** Reduce false entrypoints and tests that appear to promise
  E2E coverage while preserving all real diagnostic and fixture workflows.
- **Evidence paths:** historical `scripts/_tmp_dump_clearing_plan.py`;
  `scripts/tmp_check_graph_isolates.js`; `scripts/tmp_sse_watch.py`;
  `scripts/_archive/generate_simulator_seed_scenarios_legacy.py`;
  historical `tests/test_e2e_example.py:23` and placeholder fixture block formerly
  at `tests/conftest.py:231-244`; `.gitignore:95-131`.
- **Exact scope:** Inventory tracked temp/archive/example candidates, all call-sites,
  docs and Git history; remove only candidates with no current consumer or replace
  placeholder coverage with an existing real behavioral selector; remove fixtures
  used solely by deleted placeholders; document intentionally retained diagnostics.
- **Non-scope:** Bulk deletion of `docs/**/archive`, simulator legacy references,
  fixtures, snapshots, or failing tests; adding ignore rules to legitimize new root
  garbage.
- **Dependencies:** REN-002; coordinate gate updates with REN-003.
- **Acceptance criteria:**
  - Every deletion has recorded `references before`, replacement or reason, and
    `references after = 0`.
  - Test collection loses no unique user-observable behavior.
  - No production wrapper/import remains solely for a deleted monkeypatch hook.
  - Repository startup and documented diagnostic commands do not reference removed
    files.
- **Targeted gates:** `BACKEND-TARGETED tests/test_e2e_example.py` before deletion;
  affected replacement selectors;
  `.\.venv\Scripts\python.exe -m pytest --collect-only -q`.
- **Full gates:** `BACKEND-DEFAULT`; `BACKEND-LINT-DIAGNOSTIC`; affected frontend gates.

## REN-005 — Close deployment ENV and secret-guardrail gaps

- **Priority:** P1
- **Owner surface:** `app/config.py`, `docker-compose.yml`,
  `docker-compose.dev.yml`, `Dockerfile`, `docker/Dockerfile`, `.env.example`,
  deployment/config docs
- **Status:** IN PROGRESS (2026-08-07)
- **Working-tree evidence (2026-08-07):** startup now requires an explicit
  canonical `ENV` (with a conflict-checked legacy alias); non-dev secrets,
  canonical HTTP origins and repeated placeholders fail closed. Base Compose is
  production-like, the dev overlay and active local callers select `ENV=dev`, and
  both images share a migration-first command-preserving entrypoint. The final
  targeted selector passed `86` tests with `2` platform skips and independent
  adversarial review found no remaining P1/P2. Docker/Compose build and runtime
  smoke were not run because Docker CLI is absent, so the task is not `DONE`.
- **Rationale / value:** Compose currently leaves `Settings.ENV` at `dev`, uses an
  insecure JWT placeholder, omits admin/simulator secrets, and dev compose sets
  `ENVIRONMENT` although the application reads `ENV`.
- **Evidence paths:** `app/config.py:9-46,101-120,139-221`;
  `docker-compose.yml:30-53`; `docker-compose.dev.yml:5-23`;
  `docker/Dockerfile:1-31`; `Dockerfile:1-53`; `.env.example:1-21`;
  `docs/ru/config-reference.md:331-340`.
- **Exact scope:** Define environment naming and supported values once; ensure
  non-dev startup cannot bypass JWT/admin/session/CSRF guardrails; remove or
  explicitly assign roles to duplicate Dockerfiles; make examples non-runnable as
  production secrets; add startup/config tests for dev, test, and non-dev modes.
- **Non-scope:** Deploying infrastructure, choosing a secret manager, rotating live
  credentials, redesigning authentication, or changing unrelated simulator knobs.
- **Dependencies:** REN-002; CI coverage from REN-003 is preferred but not required
  for implementation.
- **Acceptance criteria:**
  - `ENV`/alias behavior is explicit and tested; unknown/misnamed production values
    fail safely.
  - Non-dev startup rejects every current placeholder and empty CSRF allowlist.
  - Dev/test startup remains one-command and intentionally permissive only where
    documented.
  - One canonical container build path is named; any retained alternative has a
    tested purpose.
  - No secret value is committed.
- **Targeted gates:** `BACKEND-TARGETED tests/unit/test_settings_guardrails.py` plus
  new compose/config tests; `docker compose ... config --quiet` where Docker exists.
- **Full gates:** `BACKEND-DEFAULT`; `BACKEND-LINT-DIAGNOSTIC`; container smoke in CI.

## REN-006 — Restore ORM parity with migration 017

- **Priority:** P1
- **Owner surface:** `app/db/models/simulator_storage.py`,
  `migrations/versions/017_add_owner_to_simulator_runs.py`, migration tests
- **Status:** IN PROGRESS (2026-08-07)
- **Working-tree evidence (2026-08-07):** ORM metadata now requires `owner_id`
  and declares `ix_simulator_runs_owner_state_created`; focused schema/owner tests,
  the backend default tier, and the single-head check pass. Migration 017 retains
  an intentional weaker SQLite nullability path, while fresh and 016-upgrade
  disposable PostgreSQL evidence remains outstanding; therefore parity is not
  claimed complete.
- **Rationale / value:** PostgreSQL schema makes `owner_id` non-null and creates an
  owner/state/time index, while ORM metadata says nullable and omits the index.
  Drift makes autogenerate and schema reasoning unsafe.
- **Evidence paths:** `app/db/models/simulator_storage.py:9-53`;
  `migrations/versions/017_add_owner_to_simulator_runs.py:28-61`;
  `app/core/simulator/storage.py:65-68`.
- **Exact scope:** Decide and encode the canonical cross-dialect contract for
  `owner_id`, `owner_kind`, and `ix_simulator_runs_owner_state_created`; add a
  migration/metadata parity test on SQLite and disposable PostgreSQL; if a new
  migration is required, append it rather than editing applied history.
- **Non-scope:** Converting all simulator storage to DB-first, adding the deferred
  metric/bottleneck/artifact FKs, or rewriting old migrations without evidence
  they have never shipped.
- **Dependencies:** REN-002.
- **Acceptance criteria:**
  - Model metadata and migrated PostgreSQL agree on nullability and named indexes,
    or an explicit dialect exception is tested and documented.
  - Fresh upgrade and upgrade from revision 016 both reach one head.
  - New runtime writes still reject empty owner IDs; legacy sentinel rows remain
    readable under the documented policy.
  - Autogenerate/check produces no unintended owner/index reversal.
- **Targeted gates:** `ALEMBIC-HEADS`; simulator owner/storage tests; fresh and
  incremental migration tests on disposable databases.
- **Full gates:** `BACKEND-DEFAULT`; `BACKEND-LINT-DIAGNOSTIC`; PostgreSQL migration job.

## REN-007 — Fix event-bus QueueFull handling and define backpressure

- **Priority:** P1
- **Owner surface:** `app/utils/event_bus.py`, `app/api/v1/websocket.py`, event-bus
  unit/integration tests
- **Status:** DONE (2026-08-07)
- **Working-tree evidence (2026-08-07):** enqueue/backpressure handling now runs
  inside the subscriber loop callback with deterministic drop-newest semantics,
  bounded metrics, closed-loop cleanup, and an unsubscribe cutoff. Same-loop,
  cross-thread, full-queue, unsubscribe, closed-loop, WebSocket sibling tests and
  the backend default tier pass; independent adversarial review found no remaining
  P1/P2 on this surface.
- **Rationale / value:** `QueueFull` is raised later in the callback scheduled by
  `call_soon_threadsafe`, outside the current `try`, so the documented drop policy
  does not catch the actual overload error.
- **Evidence paths:** `app/utils/event_bus.py:17-56`;
  `app/api/v1/websocket.py:1-89`;
  correct direct-queue sibling behavior at
  `app/core/simulator/sse_broadcast.py:175-224`.
- **Exact scope:** Put enqueue and overload handling in the event-loop callback;
  define drop/disconnect/coalesce policy for this bus; add bounded metrics/logging
  without high-cardinality participant labels; test same-loop, cross-thread,
  full-queue, unsubscribe, and closed-loop paths.
- **Non-scope:** Replacing the bus with Kafka/Redis pub-sub, guaranteeing durable
  delivery, or unifying simulator SSE and participant WebSocket protocols.
- **Dependencies:** REN-002; metric naming should coordinate with REN-008.
- **Acceptance criteria:**
  - A full subscriber queue produces no unhandled event-loop callback exception.
  - The selected loss policy is deterministic and observable.
  - Publish never blocks business transactions and unsubscribe cannot receive new
    scheduled messages after its defined cutoff.
  - Existing WebSocket success behavior and authorization remain unchanged.
- **Targeted gates:** new event-bus unit tests;
  `BACKEND-TARGETED tests/unit/test_websocket_payment_received_event.py`.
- **Full gates:** `BACKEND-DEFAULT`; `BACKEND-LINT-DIAGNOSTIC`.

## REN-008 — Make critical background-task failures observable

- **Priority:** P1
- **Owner surface:** `app/main.py`, `app/core/recovery.py`,
  `app/core/integrity.py`, lifespan/metrics tests
- **Status:** IN PROGRESS (2026-08-07)
- **Working-tree evidence (2026-08-07):** recovery and integrity tasks are
  supervised; start/iteration/exit failures update bounded state, logs and
  metrics; both health aliases expose degraded readiness while both healthz
  aliases remain liveness. Deterministic tests cover start failure, periodic
  failure/recovery, cancellation, unexpected exit and resource-safe lifespan
  shutdown; the backend default tier passes. A production-like container
  startup/shutdown smoke remains outstanding.
- **Rationale / value:** Recovery and integrity task creation can currently fail
  under broad `except Exception: pass`, leaving a healthy-looking process without
  required maintenance work.
- **Evidence paths:** `app/main.py:72-182,184-221`;
  `app/core/recovery.py:117-165`; `app/utils/metrics.py:38-46`.
- **Exact scope:** Classify mandatory startup failures vs degradable periodic
  failures; log and metric every degraded state with stable event names; expose
  readiness/degraded status without leaking exception text; supervise unexpected
  task exit and preserve graceful cancellation.
- **Non-scope:** A general job scheduler, distributed orchestration, alert-manager
  deployment, or making best-effort business metrics fatal.
- **Dependencies:** REN-002; REN-005 for environment policy; coordinate metrics
  with REN-007.
- **Acceptance criteria:**
  - No critical task-creation/startup exception is silently swallowed.
  - Tests cover import/create failure, first-run failure, periodic failure,
    unexpected task exit, cancellation, and clean shutdown.
  - Health/readiness distinguishes process alive from maintenance degraded.
  - Request/business behavior stays available only for failure classes explicitly
    accepted as degradable.
- **Targeted gates:** new lifespan/background supervision tests;
  `BACKEND-TARGETED tests/unit/test_recovery_cleanup.py
  tests/unit/test_integrity_checkpoints.py tests/unit/test_settings_guardrails.py`.
- **Full gates:** `BACKEND-DEFAULT`; `BACKEND-LINT-DIAGNOSTIC`; container startup/shutdown smoke.

## REN-009 — Strengthen the OpenAPI contract beyond paths and methods

- **Priority:** P1
- **Owner surface:** `api/openapi.yaml`, FastAPI route/schema declarations,
  `tests/contract/test_openapi_contract.py`
- **Status:** IN PROGRESS (2026-08-07)
- **Working-tree evidence (2026-08-07):** the contract compares semantic
  parameters (including full auth transport shape), request/response media and
  schemas, statuses, error envelopes and security OR/AND/scopes under exact
  count+digest ratchets. Simulator actions now distinguish flat body-validation
  `400` from identity-transport `422 ErrorEnvelope`; both health aliases share a
  typed `ok|degraded` response. The canonical contract selector passed `16`
  tests and two independent adversarial passes found no remaining P1/P2. A
  published CI contract job has not run, so the task remains `IN PROGRESS`.
- **Rationale / value:** The current contract test passes while checking only path
  and method sets; it does not protect parameters, request/response schemas,
  statuses, or security.
- **Evidence paths:** `tests/contract/test_openapi_contract.py:34-86`;
  `api/openapi.yaml`; `app/api/router.py:6-21`; `app/main.py:286-323`.
- **Exact scope:** Compare normalized operations for parameters, required request
  bodies, success/error statuses, response schema references, and security; add
  explicit exceptions only for documented helper schemas; verify Pydantic aliases
  and simulator action error-envelope divergence.
- **Non-scope:** Rewriting every schema for stylistic parity, changing public API
  semantics to match stale prose, requiring byte-identical generated YAML, or
  testing descriptions/examples as behavior.
- **Dependencies:** REN-002; REN-003 for CI; land before API-moving portions of
  REN-010 and REN-012.
- **Acceptance criteria:**
  - A deliberate mutation of a required parameter, response model/status,
    security declaration, or `from` alias makes the contract test fail with an
    actionable diff.
  - Root health and `/api/v1` exposure are explicitly classified rather than
    accidentally double-counted.
  - The canonical source and update workflow are stated; helper-only schemas do
    not create false failures.
- **Targeted gates:** `OPENAPI`; focused alias/SSE serialization tests.
- **Full gates:** `BACKEND-DEFAULT`; `BACKEND-LINT-DIAGNOSTIC`; CI contract job.

## REN-010 — Establish one unit-of-work owner for payments, clearing, and admin writes

- **Priority:** P1
- **Owner surface:** `app/api/v1/payments.py`, `app/api/v1/clearing.py`, write paths
  in `app/api/v1/admin.py`, `app/core/payments/`,
  `app/core/clearing/service.py`, `app/core/trustlines/service.py`
- **Status:** IN PROGRESS (2026-08-07)
- **Committed slice evidence (2026-08-07):** the first vertical slice replaces the
  simulator's implicit `commit=False` convention with an explicit staged payment
  result and ordered post-commit/rollback journal. Outer tick owners now resolve
  DB effects, cache invalidation, metrics and SSE only after the observed
  transaction outcome. Shared commit/rollback resolvers drain terminal DB
  outcomes through repeated cancellation and classify commit, rollback or
  unknown exactly once; idempotent replay, rollback-failure and cancelled-commit
  observations have behavioral coverage. Runtime Admin config validates a whole
  batch before work, serializes overlapping config/legacy-feature writers,
  requires durable audit before publishing values, and rejects coercive types.
  The final canonical six-file remediation selector passed `52` tests. Claude
  Code `2.1.224` reviewed frozen `f839311..f068e72` with resolved
  `claude-opus-5` at high effort and complete exit-`0` JSON; its cancelled-commit
  P2 was fixed in `d05c27a`, then independently reviewed with no remaining P1/P2.
  Its audit/publication P2 maps to the already accepted residual below rather
  than a new task. The next payment slice reuses the sorted transaction advisory
  locks for single-route capacity reads and adds a mutation-sensitive two-session
  PostgreSQL concurrency test. Its design passed independent review, while the
  PostgreSQL execution remains unverified because no disposable PostgreSQL or
  Docker runtime is available locally. Payment prepare/commit/timeout failures
  now preserve typed 4xx codes, sanitize internal failures to `E010/500` across
  HTTP, the committed abort path, GET/list/retry and service log messages. The
  changed prepare/commit/timeout service cleanup paths stop recovery reads and
  abort attempts after their rollback fails, while staged calls retain outer-UoW
  ownership. The combined payment taxonomy-and-regression selector passed `48`
  tests across the taxonomy, timeout, abort-code, insufficient-capacity, ordered
  simulator journal and OpenAPI contract files; independent remediation review
  found no remaining P1/P2 in that slice. External review of the frozen payment
  range `f45b8bb..d35bc02` confirmed the taxonomy work and exposed an incomplete
  prepare/commit lock protocol. Remediation `a334c46` now serializes each tx state
  machine before globally sorted segment locks, uses one strict persisted-flow
  representation for commit keys/effects, keeps recovery abort compatible with
  malformed legacy locks, and prevents duplicate prepare/commit/abort terminal
  transitions. Its final local selector passed `36` tests and discovered `7`
  PostgreSQL-only scenarios, all skipped without a live PostgreSQL runtime;
  independent review found no remaining P1/P2 in the frozen fix. Normal API
  contention against simulator-held transaction locks remains an accepted P2:
  requests are bounded by the existing prepare timeout, while skipping locks or
  moving the simulator commit boundary would weaken integrity or expand tick UoW
  semantics. Process-crash delivery remains intentionally best-effort (no
  outbox), audit-before-publish retains a documented crash/ambiguous-commit
  window, and the broader public payment/clearing UoW map plus PostgreSQL
  execution/ambiguous connection-loss evidence remain outstanding.
- **Rationale / value:** Commits/rollbacks currently occur in API, services, and
  engine, while `commit: bool` asks callers to understand nested SQLAlchemy
  contexts. This is the highest-integrity refactor and must follow behavior locks.
- **Evidence paths:** `app/api/v1/admin.py:255-400`;
  `app/core/simulator/commit_resolution.py:9-159`;
  `app/core/simulator/real_tick_clearing_coordinator.py:67-94`;
  `app/core/simulator/real_tick_persistence.py:51-144`;
  `app/core/simulator/real_tick_trust_drift_coordinator.py:28-87`;
  `app/core/simulator/real_tick_orchestrator.py:341-382`;
  `tests/unit/test_admin_config_patch_atomicity.py:182-523`;
  `tests/unit/test_real_tick_commit_cancellation.py:183-480`;
  `app/core/payments/service.py:545-848`;
  `app/core/payments/engine.py:75-180,299-737,764-1114,1342-1540`;
  `tests/integration/test_concurrent_prepare_routes_bottleneck_postgres.py`;
  `tests/integration/test_payment_commit_advisory_locks_postgres.py`;
  `tests/integration/test_payment_prepare_error_taxonomy.py`;
  `tests/unit/test_payment_engine_advisory_locks_execute.py`;
  `tests/unit/test_payments_2pc.py:185-380`;
  `tests/unit/test_recovery_cleanup.py`;
  `tests/unit/test_payment_timeouts.py`;
  `tests/integration/test_payment_abort_has_error_code.py`;
  `tests/integration/test_payments_insufficient_capacity.py`;
  `tests/unit/test_real_payments_ordered_journal.py`;
  `tests/contract/test_openapi_contract.py:313-349`;
  `api/openapi.yaml:2246-2321`;
  `app/core/clearing/service.py:772-1060`;
  `app/core/trustlines/service.py:108-139,200-243,288-330`.
- **Exact scope:** First map every write call-site and transaction state transition;
  characterize idempotency/retry/abort/timeout behavior with tests; select one UoW
  boundary per public operation; move commit/rollback responsibility without
  changing domain semantics; make nested simulator execution an explicit adapter
  or transaction context rather than a boolean convention.
- **Non-scope:** Replacing SQLAlchemy, changing routing/clearing algorithms,
  relaxing invariants, combining independent failures, or optimizing before
  correctness is locked.
- **Dependencies:** REN-006 and REN-009; REN-003 required before the first behavior
  change.
- **Acceptance criteria:**
  - Every write operation has one named UoW owner; lower layers do not commit
    unexpectedly and API handlers do not repair internal transaction state.
  - Success, validation failure, DB failure, serialization/deadlock retry, timeout,
    cancellation, duplicate tx ID, abort, and simulator nested-call paths have
    observable-effect tests.
  - No raw internal exception is converted to a client 4xx solely by broad catch.
  - PostgreSQL lost-update/advisory/row-lock tests pass; SQLite tests are not cited
    as proof of locking semantics.
  - Cache invalidation and audit/integrity writes occur at the correct post-commit
    boundary.
- **Targeted gates:** payment/clearing/trustline unit and integration selectors;
  `POSTGRES-CONCURRENCY` for
  `tests/integration/test_concurrent_clearing_payment_lost_update.py`,
  `test_concurrent_prepare_routes_bottleneck_postgres.py`, and
  `test_payment_engine_uow_retry_postgres.py`.
- **Full gates:** `BACKEND-DEFAULT`; `BACKEND-LINT-DIAGNOSTIC`; `OPENAPI`; scheduled PostgreSQL
  suite; independent adversarial review before merge.

## REN-011 — Enforce latest-request-wins in Admin UI data loading

- **Priority:** P1
- **Owner surface:** `admin-ui/src/api/realApi.ts`, page/composable loaders under
  `admin-ui/src/`, their Vitest suites
- **Status:** IN PROGRESS (2026-08-07)
- **Working-tree evidence (2026-08-07):** a narrow generation-token primitive is
  applied to confirmed overlapping page/graph loaders; debounce/throttle disposal
  and a single graph view-request owner prevent stale data/error/loading/rebuild
  state. Graph selection is now visually independent of cycle-data request
  ownership and is restored after Cytoscape rebuilds. Reverse-resolution,
  mixed load/refresh/focus and both selection resolution orders pass; the final
  graph selector passed `18` tests, Admin build passed and lint reported zero
  errors (known warnings remain). Independent cross-review found no remaining
  P1/P2. The affected Playwright route/filter flow was not run because the local
  Chromium executable is absent.
- **Rationale / value:** The API layer has timeout cancellation, but no repository-
  wide evidence that rapidly changing filters/routes prevent an older response
  from overwriting newer state. This is a common operator-visible race, not a
  reason to wrap every request blindly.
- **Evidence paths:** `admin-ui/src/api/realApi.ts:388`;
  `admin-ui/src/pages/`; `docs/ru/development-standards.md:523-588` (A16 stale async
  results); existing route-query synchronization docs and page tests.
- **Exact scope:** Inventory loaders triggered by route/filter/search changes;
  reproduce stale overwrite on each affected shared path; implement AbortController
  or monotonic request generation at the narrow owner; normalize loading/error
  finalization so only the latest request mutates visible state.
- **Non-scope:** A new global data-fetching framework, cancellation for fire-and-
  forget writes without an observed race, redesigning page UX, or masking backend
  latency.
- **Dependencies:** REN-002; REN-003 for frontend CI.
- **Acceptance criteria:**
  - Deterministic deferred-promise tests show response B remains visible when
    older response A resolves/rejects after B.
  - Stale requests cannot clear the latest loading/error/data state.
  - Abort is not shown as an operator error; genuine latest-request failure is.
  - Every loader changed is listed; unaffected loaders are explicitly non-scope.
- **Targeted gates:** affected Admin Vitest files with deterministic races.
- **Full gates:** `ADMIN-UNIT`; `ADMIN-BUILD`; relevant Playwright route/filter
  smoke if behavior crosses routing.

## REN-012 — Create simulator boundaries and close accessibility gaps

- **Priority:** P1
- **Owner surface:** `app/api/v1/simulator.py`, `app/core/simulator/`,
  `app/schemas/simulator.py`, `simulator-ui/v2/src/components/`,
  simulator API/client normalization
- **Status:** PLANNED
- **Rationale / value:** Simulator API and runtime remain the largest coupled
  backend surface, while accessible keyboard/focus semantics must survive any UI
  boundary extraction. Treat this as two reviewable sub-slices under one protected
  simulator contract, not a rewrite.
- **Evidence paths:** `app/api/v1/simulator.py:1-2620`;
  `app/core/simulator/runtime.py:1-15`;
  `app/core/simulator/runtime_impl.py:81-983`;
  `app/core/simulator/real_runner_impl.py:41-551`;
  `app/schemas/simulator.py`; `simulator-ui/v2/src/components/BottomBar.vue:130-299`;
  `simulator-ui/v2/src/components/common/OverlaySelect.vue:199-254`;
  existing focus/Escape tests.
- **Exact scope:** (A) extract cohesive HTTP action/query/SSE adapters from the
  2620-line router while preserving the public runtime facade and owner/CSRF
  checks; make task/session/storage dependencies explicit where evidence supports
  it. (B) audit interactive simulator overlays/windows for semantic role/name,
  keyboard open/close/navigation, focus entry/trap/restore, disabled/busy
  announcement, and reduced-motion behavior; fix shared primitives before
  one-off panels.
- **Non-scope:** Rewriting the simulator engine, merging fixtures and real modes,
  changing event schemas or visual design without a product decision, enforcing
  arbitrary file-size limits, or broad WCAG claims beyond tested flows.
- **Dependencies:** REN-009 before router movement; REN-010 before moving write
  transaction boundaries; REN-003 for frontend/backend gates.
- **Acceptance criteria:**
  - Router handlers are transport adapters; domain mutation, DB transaction, run
    lifecycle, and SSE broadcasting have named owners with no new import cycle.
  - Public paths, payloads, aliases, owner isolation, CSRF, strict replay, and
    restart/stop/error state transitions remain contract-tested.
  - Each changed interactive primitive works by keyboard, has an accessible name
    and correct role/state, moves focus intentionally, and restores focus on close.
  - Automated accessibility tests are paired with a short manual keyboard/screen-
    reader checklist; claims are limited to audited flows.
  - Each sub-slice is mergeable and reversible independently.
- **Targeted gates:** simulator action/owner/CSRF/SSE/runtime selectors; affected
  component Vitest focus tests; API normalization tests.
- **Full gates:** `BACKEND-DEFAULT`; `BACKEND-LINT-DIAGNOSTIC`; `OPENAPI`; `SIM-UNIT`; simulator
  super-smoke only at milestone completion; relevant Playwright keyboard smoke.

## REN-013 — Make test taxonomy and database isolation truthful

- **Priority:** P1
- **Owner surface:** `tests/`, `pytest.ini`, test fixtures, simulator/admin test
  configs, README testing section
- **Status:** IN PROGRESS (2026-08-07)
- **Partial evidence (2026-08-07):** destructive DB URLs and pytest option/path
  injection are rejected before collection; SQLite DB, basetemp and artifact
  roots are task-local; super-smoke is marked `slow` and scheduled separately.
  Eight proven-stale Simulator assertions were aligned to the already-shipped
  `payment=440px` and `OverlaySelect` contracts. The default backend tier passed
  (`510 passed`, `5 skipped`, `4` expensive deselected), sequential `SIM-UNIT`
  passed `637` tests, and Simulator build exited `0`. This does not complete the
  broader taxonomy, parallel-worker, PostgreSQL and E2E scope below.
- **Rationale / value:** Tests named unit/integration/e2e currently mix TestClient,
  DB setup, placeholders, and dialect-specific semantics. A truthful taxonomy
  makes gates fast and prevents SQLite success from being reported as concurrency
  proof.
- **Evidence paths:** `pytest.ini:1-31`; `tests/conftest.py`;
  historical `tests/test_e2e_example.py`; `tests/unit/`; `tests/integration/`;
  PostgreSQL selectors in `tests/integration/*postgres*.py`;
  `README.md:298-410`.
- **Exact scope:** Classify tests by external resources and semantic ownership;
  mark fast/dialect/Postgres/slow/e2e explicitly; isolate DB, module singletons,
  event loops, runtime tasks, caches, files, and env overrides; make selectors
  deterministic and collection-safe; retire duplicate tests only after behavior
  coverage mapping.
- **Non-scope:** Chasing a coverage percentage, converting all tests to one style,
  deleting slow tests because they are slow, or making production code expose
  internals solely for tests.
- **Dependencies:** REN-003 and REN-004; incorporate new tests from REN-006 through
  REN-012 as those slices land.
- **Acceptance criteria:**
  - Each marker has an executable definition and CI tier; unknown markers fail.
  - Fast suite performs no network access and no nondisposable external DB writes.
  - PostgreSQL locking claims appear only in the disposable PostgreSQL tier.
  - Repeated and reordered runs leave no simulator tasks, subscriptions, cache,
    files, env, or DB state for the next test.
  - Test names describe observable behavior and owner surface, not implementation
    wiring.
- **Targeted gates:** collect-only per marker; repeat selected singleton/DB suites
  in different orders; dedicated Postgres safety-guard negative test.
- **Full gates:** all CI tiers; `BACKEND-DEFAULT`; frontend unit/build gates; scheduled
  PostgreSQL and expensive simulator milestones.

## REN-014 — Rebuild documentation source-of-truth, archive, and translation flow

- **Priority:** P1
- **Owner surface:** `README.md`, `docs/en/`, `docs/ru/`, `docs/pl/`, archive
  indexes, config/deployment/API/simulator architecture docs
- **Status:** PLANNED
- **Rationale / value:** Current architecture prose describes planned modules that
  do not exist, current and archived simulator specs coexist, and translations can
  drift independently. Operators and agents need navigation and status, not a
  mechanical rewrite of every historical document.
- **Evidence paths:** `docs/en/03-architecture.md:123-216`;
  `docs/ru/03-architecture.md`; `docs/ru/config-reference.md:331-340`;
  `docs/ru/simulator/backend/`; `docs/**/archive/`;
  `docs/ru/documentation-rules.md`; `README.md:571-640`.
- **Exact scope:** Declare current implementation docs, target/concept docs, and
  historical/archive docs; add status/date/owner-surface and successor links;
  update current architecture, configuration, deployment, API, testing, and
  simulator boundaries from accepted code; define a primary-language and
  translation synchronization policy with explicit lag markers.
- **Non-scope:** Translating all archives, deleting history, making wording tests,
  presenting future architecture as implemented, or changing code solely to match
  stale diagrams.
- **Dependencies:** REN-002; update after accepted behavior slices REN-005 through
  REN-013, but archive/index work can proceed independently.
- **Acceptance criteria:**
  - From README/AGENTS, a reader can reach one current document for runtime,
    architecture, API, configuration, testing, and simulator behavior.
  - Historical documents are visibly historical and link to their current
    successor where one exists.
  - Current docs cite executable commands/owners rather than volatile counts.
  - Translation status is visible; no untranslated or stale page silently claims
    parity.
  - Spot-checks against code, migrations, OpenAPI, package manifests, and Git
    history find no known contradiction in changed current docs.
- **Targeted gates:** link checker if available; manual source-of-truth matrix;
  command/path verification; OpenAPI/config/migration spot-check.
- **Full gates:** documentation review by backend, frontend, and operator owners;
  product gates only when examples execute code.

## REN-015 — Final adversarial review and renovation closeout

- **Priority:** P1
- **Owner surface:** all changed surfaces, spec changelog, accepted-debt register
- **Status:** PLANNED
- **Rationale / value:** Verify effects across sibling paths and catch regressions
  introduced by the renovation itself before declaring the old-code cleanup done.
- **Evidence paths:** diffs and acceptance evidence for REN-001 through REN-014;
  `git log` for each owner surface; CI artifacts; spec changelog and known-open
  decisions.
- **Exact scope:** Independent reviewers re-check architecture direction,
  transactions, concurrency/cancellation, security/config, API compatibility,
  accessibility flows, test isolation, repository inventory, and documentation
  claims; reproduce every actionable finding before fixing; run final gates on a
  clean checkout; record accepted ceilings and remaining work with owner decision.
- **Non-scope:** Starting a second broad redesign, fixing unrelated rare edge cases,
  reopening explicitly accepted ceilings without new evidence, or treating a green
  aggregate metric as sufficient review.
- **Dependencies:** All tasks selected for this renovation milestone are `DONE` or
  explicitly `ACCEPTED-NOT-DOING` with dated owner rationale.
- **Acceptance criteria:**
  - Independent review is performed from fresh context and reports file/line or
    reproducible-test evidence for each finding.
  - Every finding is verified, fixed in its owner slice, rejected with evidence, or
    recorded as accepted debt by the owner.
  - All canonical gates pass on a clean checkout; CI reaches a final state.
  - Current docs and spec changelog reference the actual merge SHAs and fresh gate
    results.
  - No secret, user artifact, temporary database, test output, or generated bundle
    is newly tracked.
- **Targeted gates:** rerun each changed task's targeted gates and adversarial
  reproductions.
- **Full gates:** `REPO-FULL`; all CI tiers; disposable PostgreSQL concurrency and
  migration jobs; simulator super-smoke; relevant Admin/Simulator Playwright
  flows; clean-tree and tracked-artifact audit.

## Dependency summary

```text
REN-001 → REN-002 → REN-003
                  ├─ REN-004
                  ├─ REN-005
                  ├─ REN-007 → REN-008
                  ├─ REN-009 ─┐
REN-002 → REN-006 ─────────────┼→ REN-010 ─┐
REN-003 → REN-011              │           ├→ REN-012
REN-003 + REN-004 ─────────────┴→ REN-013 ─┘
accepted implementation slices → REN-014 → REN-015
```

The diagram is sequencing guidance, not authority to bundle all predecessors into
one PR. Parallel tasks must have non-overlapping write surfaces, and shared
contract files must be assigned to one agent at a time.
