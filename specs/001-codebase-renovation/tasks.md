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

## Execution freeze and authoritative remaining order

**Phase 1 is current.** On 2026-08-07 the repository owner approved the frozen
plan at exact commit `8f271693e7b763856d86fb3c2f579a56938d6fcb`.
Implementation is authorized only for the Phase 1 owner surfaces and results
listed below; later phases remain paused until the Phase 1 exit criteria pass.
This approval does not authorize a Git push or `workflow_dispatch`; those require
a separate recorded owner authorization naming the remote, branch and trigger.

GEOv0 is an MVP community hub/simulator for roughly 10–500 participants, not a
banking or HA platform. The authoritative remaining sequence is:

| Order | Plan phase | Existing tasks | Required result before advancing |
|---:|---|---|---|
| 0 | Plan approval | REN-002 governance | Three planning files agree; independent reviews complete; owner approves |
| 1 | Truthful gates/runtime | REN-003, 005, 006, 008, REN-013A | Published workflow; one production-like boot; empty and 016→head PostgreSQL migration |
| 2 | Backend ownership/integrity | REN-010A, conditional REN-012A | Write-path owner map; only confirmed defects fixed; bounded live-PG concurrency matrix |
| 3 | REST/SSE contracts | REN-009, REN-012B1 | Selected Admin and Simulator consumers reject malformed/unknown input deterministically |
| 4 | Admin operator paths | REN-011, REN-010B | Async proof, selected mutations, practical graph keyboard path, Chromium smoke |
| 5 | Simulator v2 paths | REN-012B2, REN-012C | Selected event families separated at decoder/state/effect seam; critical browser paths |
| 6 | Test/repository/docs cleanup | REN-013B, REN-014, REN-016 | Root artifact producers fixed; evidence-based cleanup; current docs reconciled |
| 7 | Closure | REN-015 | Bounded evidence matrix, internal reviews and Claude product-batch reviews |

Detailed work, dependencies, gates and stop criteria are authoritative in
`plan.md`. If an older task paragraph below asks for a broader matrix, full module
decomposition or platform-wide proof, the proportional scope and stop rule in the
current plan take precedence.

Parent status rule: REN-010 closes after REN-010A backend ownership plus REN-010B
Admin integration; REN-012 closes after conditional REN-012A is either completed
or explicitly not triggered and REN-012B1/B2/C complete; REN-013 closes after
REN-013A gate isolation and REN-013B touched-suite cleanup.

### Program-wide ACCEPTED-NOT-DOING

- multi-replica/HA/multi-region, automatic failover and formal RPO/RTO;
- bank-grade ledger redesign, outbox/exactly-once events and exhaustive fault
  interleavings;
- RBAC/SSO/MFA or a complete auth-platform redesign;
- full migration-history/downgrade matrix;
- new repository/UoW/state/data-fetching frameworks;
- LOC-driven decomposition of all large backend/frontend modules;
- all-browser/mobile/WCAG certification and visual redesign;
- formal performance/load program without a failed representative MVP smoke;
- coverage-percentage goals and wholesale test/source-text rewrites;
- mandatory deletion of Simulator v1, archives or duplicate Dockerfiles;
- exhaustive translation/document rewrite.

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
- `ADMIN-LINT`: `npm --prefix admin-ui run lint`
- `ADMIN-BUILD`: `npm --prefix admin-ui run build`
- `ADMIN-E2E`: `npm --prefix admin-ui run e2e`
- `SIM-TYPECHECK`: `npm --prefix simulator-ui/v2 run typecheck`
- `SIM-UNIT`: `npm --prefix simulator-ui/v2 run test:unit`
- `SIM-BUILD`: `npm --prefix simulator-ui/v2 run build`
- `SIM-E2E`: `npm --prefix simulator-ui/v2 run test:e2e`
- `REPO-FULL`: `.\scripts\verify_local.ps1 -TaskSlug <unique-task>`; Ruff/Black
  remain separately reported diagnostics and are not described as enforced.
- `POSTGRES-CONCURRENCY`: the named selector against a dedicated disposable
  PostgreSQL database only; never point `TEST_DATABASE_URL` at developer data.

A completed published `workflow_dispatch` job with retained run URL/logs is equal
evidence to a local disposable run when it executes the same frozen selector.
Configured, skipped or cancelled jobs are not evidence. Phase 1 owns adding the
missing container-smoke job and the 016→head PostgreSQL step.

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
  unit tier passed `637` tests and production build exited `0`. The canonical
  PostgreSQL tier now uses the registered `postgres` marker through
  `verify_local.ps1 -BackendMarker postgres` instead of a per-file CI allowlist;
  collect-only selected `10` tests, and the final local backend tier passed
  (`687 passed`, `3 skipped`, `14` PostgreSQL/expensive deselected). The marker tier now
  rejects a missing or SQLite test URL before pytest instead of succeeding through
  dialect skips. Local collection is not PostgreSQL runtime evidence. Disposable PostgreSQL,
  Playwright jobs, and a published GitHub Actions run remain unverified.
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
- **Dependencies:** REN-002; REN-003 for CI. Semantic parity has already landed;
  REN-010A may proceed, but any change to a maintained REST payload must extend the
  REN-009 contract test in the same commit. REN-012B1 remains the selected
  frontend-ingress continuation.
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
  transitions. Follow-ups `151b48e` and `758bb31` preserve invariant-abort lock
  ownership in both public and staged modes. The final local selector passed `36`
  tests and discovered `7`
  PostgreSQL-only scenarios, all skipped without a live PostgreSQL runtime;
  the post-remediation canonical backend milestone passed `681` tests with `13`
  skipped and `4` expensive tests deselected. Independent review found no
  remaining P1/P2 in the frozen fix. Normal API contention against
  simulator-held transaction locks remains an accepted P2:
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
- **Owning sub-slices:** REN-010A (Phase 2) owns backend owner maps and any
  confirmed transaction/cache/audit remediation. REN-010B (Phase 4) owns only the
  integration evidence that selected Admin operator flows expose the accepted
  durable result; it does not duplicate backend transaction tests.
- **Remaining exact scope after plan calibration (REN-010A):** map only the
  supported public payment, clearing, trustline, destructive Admin, destructive
  Integrity-repair and Simulator-nested write paths.
  Assign `FIX` or `KEEP`; change only a reproduced split owner, pre-commit visible
  effect, stale cache/lost update, misleading swallowed failure or promised audit
  that is not durable. Preserve current facades and use-case-local transaction
  ownership; no generalized UoW/repository layer.
- **Non-scope:** Replacing SQLAlchemy, changing routing/clearing algorithms,
  relaxing invariants, combining independent failures, or optimizing before
  correctness is locked.
- **Dependencies:** REN-006 and REN-003. Existing REN-009 semantic parity is
  sufficient to begin REN-010A; any maintained REST payload change extends the
  REN-009 contract test in the same commit.
- **Acceptance criteria:**
  - Each listed supported write path has a compact owner/effect map and a `FIX` or
    `KEEP` decision; unrelated writes are not pulled into the slice.
  - Each changed path covers normal success, one meaningful rejection, pre-commit
    DB/internal failure, applicable cancellation/rollback, and no visible
    cache/event/audit side effect after rollback.
  - Payment retains duplicate `tx_id`/terminal-state evidence; other paths do not
    receive speculative idempotency machinery.
  - No raw internal exception is converted to a client 4xx solely by broad catch.
  - The bounded PostgreSQL matrix passes: same-capacity payments, payment versus
    clearing, duplicate payment `tx_id`, plus one Admin lost-update case only if the
    owner map confirms it. No HA/network-partition claim is made.
  - Cache invalidation and audit/integrity writes occur at the correct post-commit
    boundary.
  - Both maintained Integrity repair operations have a `FIX` or `KEEP` decision
    backed by success, pre-commit rollback and audit/cache/publication evidence.
- **Targeted gates:** payment/clearing/trustline unit and integration selectors;
  `POSTGRES-CONCURRENCY` for
  `tests/integration/test_concurrent_clearing_payment_lost_update_postgres.py`,
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

## REN-012 — Make the selected Simulator v2 paths testable and keyboard usable

- **Priority:** P1 for trusted ingress/state ordering and supported keyboard paths;
  P2/accepted debt for all other decomposition.
- **Owner surface:** Simulator HTTP/SSE normalization, real-mode replay/state/effect
  path, existing frontend facades, selected UI/window primitives; backend router
  only when a confirmed transaction/contract fix needs an extraction seam.
- **Status:** PLANNED — split into REN-012A/B/C below.
- **Rationale / value:** The material risk is not file length. It is that unchecked
  input, replay logic, state mutation and visual effects are difficult to verify
  independently, while canvas-only selection blocks keyboard use. The target is a
  narrow trusted-event seam and practical access to current MVP workflows.
- **Dependencies:** truthful Simulator gates from REN-003; selected contracts from
  REN-009; backend transaction ownership from REN-010 before moving write behavior.

### REN-012A — Conditional backend adapter

- **Status:** PLANNED / execute only if triggered.
- **Exact scope:** when REN-009/010 work repeatedly crosses the same router-owned
  lifecycle/action boundary, extract one route-independent adapter behind the
  existing public runtime facade. Preserve owner/CSRF, paths, payloads and SSE.
- **Acceptance:** affected handlers become thin enough to test the confirmed
  behavior once; all callers stay on the existing facade; no import cycle.
- **Stop:** no 2,600-line router rewrite, no target architecture by LOC, and no
  extraction if the confirmed fix remains clearer locally.

### REN-012B — Frontend ingress, replay, state and effect seam

- **Status:** PLANNED. REN-012B1 contract/ingress is owned by Phase 3;
  REN-012B2 replay/state/effect migration is owned by Phase 5.
- **Exact sequence:**
  1. Characterize scenario/run-status/snapshot HTTP responses and lifecycle,
     topology, payment and clearing SSE families.
  2. Validate those HTTP responses at endpoint ingress.
  3. Make malformed known and unknown SSE events diagnostic and non-mutating.
  4. Isolate replay cursor/dedup, stale-run and `410` refresh decisions.
  5. Make accepted-event state application testable without canvas/time.
  6. Produce small effect intents after accepted state change; never run FX for a
     stale, duplicate, malformed or rejected event.
  7. Migrate event families one at a time through the existing facade:
     lifecycle → topology → payment → clearing.
- **Acceptance sequences:** start/stop/restart/error; duplicate; reconnect; stale
  run; `410`; malformed/unknown; topology patch; payment success/failure/cancel;
  clearing patch/completion; state-before-effect ordering.
- **Stop:** the four families are deterministic behind the facade. Remaining large
  composables may stay large. No new state framework, immutable-state doctrine or
  wire-schema redesign.

### REN-012C — Critical Simulator functionality and accessibility

- **Status:** PLANNED.
- **Functional paths:** fixture bootstrap/switch; real preview/stale recovery;
  start/stop/restart/error; payment success plus one rejection/cancel; trustline
  create/edit/blocked close; clearing preview/confirm/result; node/edge inspect.
- **Exact scope:** provide a DOM-based node/edge navigator over existing data;
  keyboard entry to current payment/trustline/clearing forms; focus entry/restore
  for changed windows; busy/error/success announcements; decorative FX canvas
  hidden from assistive technology; reduced motion disables optional FX.
- **Acceptance:** unit/component behavior plus a short non-visual Chromium keyboard
  smoke proves the named paths. Claims are limited to those paths.
- **Stop:** no full WCAG/screen-reader/browser certification, no complex ARIA canvas
  model, no visual redesign and no manual audit of every overlay.

- **Targeted gates:** selected HTTP/event normalizer, replay/state/effect and
  component/focus tests; affected backend simulator selectors only for REN-012A.
- **Full gates:** `SIM-TYPECHECK`; `SIM-UNIT`; `SIM-BUILD`; correctness-focused
  Simulator lint ratchet; `SIM-E2E` scoped to the named flows; `OPENAPI` and
  `BACKEND-DEFAULT` only when the backend/wire contract changes.

## REN-013 — Make test taxonomy and database isolation truthful

- **Priority:** P1
- **Owner surface:** `tests/`, `pytest.ini`, test fixtures, simulator/admin test
  configs, README testing section
- **Status:** IN PROGRESS (2026-08-07)
- **Owning sub-slices:** REN-013A (Phase 1) owns truthful marker/gate selection and
  two-task-slug resource isolation. REN-013B (Phase 6) owns cleanup of false-signal
  or leaked-state tests touched by the accepted product slices.
- **Partial evidence (2026-08-07):** destructive DB URLs and pytest option/path
  injection are rejected before collection; SQLite DB, basetemp and artifact
  roots are task-local; super-smoke is marked `slow` and scheduled separately.
  PostgreSQL-only integration modules now follow the guarded
  `test_*_postgres.py` + module-marker policy, and the scheduled/manual job calls
  the canonical runner with `-BackendMarker postgres -BackendSelector
  tests/integration`. Collect-only selected all `10` current PostgreSQL tests;
  the same canonical command now fails before pytest when its URL is missing or
  SQLite, preventing a skip-only false-green PostgreSQL job. The
  final local backend tier passed (`687 passed`, `3 skipped`, `14`
  PostgreSQL/expensive deselected); sequential `SIM-UNIT` previously passed `637` tests and Simulator
  build exited `0`. Parallel-worker, live PostgreSQL and E2E evidence remain.
- **Rationale / value:** Tests named unit/integration/e2e currently mix TestClient,
  DB setup, placeholders, and dialect-specific semantics. A truthful taxonomy
  makes gates fast and prevents SQLite success from being reported as concurrency
  proof.
- **Evidence paths:** `pytest.ini:1-31`; `tests/conftest.py`;
  historical `tests/test_e2e_example.py`; `tests/unit/`; `tests/integration/`;
  PostgreSQL selectors in `tests/integration/*postgres*.py`;
  `README.md:298-410`.
- **Remaining exact scope:** keep the existing marker/DB guardrails; prove two
  canonical runs with different task slugs do not share DB/temp/artifact state;
  repair leaked singleton/event-loop/timer/file state only in suites touched by the
  remaining plan; retire a duplicate/source-text test only when the changed
  behavior has stronger coverage. Full-suite taxonomy and `xdist` migration are
  not required.
- **Non-scope:** Chasing a coverage percentage, converting all tests to one style,
  deleting slow tests because they are slow, or making production code expose
  internals solely for tests.
- **Dependencies:** REN-003 and REN-004; incorporate new tests from REN-006 through
  REN-012 as those slices land.
- **Acceptance criteria:**
  - Each marker has an executable definition and CI tier; unknown markers fail.
  - Fast suite performs no network access and no nondisposable external DB writes.
  - PostgreSQL locking claims appear only in the disposable PostgreSQL tier.
  - Repeated and reordered changed selectors leave no simulator tasks,
    subscriptions, cache, files, env, or DB state for the next test.
  - New behavior tests describe observable behavior; existing architecture guards
    may keep source/import assertions when explicitly labelled.
- **Targeted gates:** collect-only per marker; repeat selected singleton/DB suites
  in different orders; dedicated Postgres safety-guard negative test.
- **Full gates:** required published workflow; `BACKEND-DEFAULT`; affected frontend
  unit/build gates; selected scheduled PostgreSQL and scoped Simulator milestones
  from the frozen plan.

## REN-014 — Rebuild documentation source-of-truth, archive, and translation flow

- **Priority:** P1
- **Owner surface:** `README.md`, `docs/en/`, `docs/ru/`, `docs/pl/`, archive
  indexes, config/deployment/API/simulator architecture docs
- **Status:** IN PROGRESS (2026-08-07)
- **Partial evidence (2026-08-07):** Root and documentation indexes now expose a
  single classified path to runtime, architecture intent, OpenAPI,
  configuration, canonical testing and Simulator domains. Current RU decision
  documents, EN/PL translations, target/concept material and archives have an
  explicit precedence policy; the RU overview is labelled vision/target rather
  than current implementation evidence. Simulator backend/frontend indexes
  replace confirmed broken or archive-promoting front-door links. A focused scan
  resolved all `79` local links in the changed front doors, and independent
  adversarial review closed two stale README navigation/authority links. Broader
  architecture/config body reconciliation, archive successor mapping and
  per-document translation status remain; this entry does not complete REN-014.
- **Rationale / value:** Current architecture prose describes planned modules that
  do not exist, current and archived simulator specs coexist, and translations can
  drift independently. Operators and agents need navigation and status, not a
  mechanical rewrite of every historical document.
- **Evidence paths:** `docs/README.md`; `docs/ru/documentation-rules.md`;
  `docs/ru/simulator/README.md`; `docs/ru/simulator/backend/README.md`;
  `docs/ru/simulator/frontend/README.md`;
  `docs/en/03-architecture.md:123-216`;
  `docs/ru/03-architecture.md`; `docs/ru/config-reference.md:331-340`;
  `docs/ru/simulator/backend/`; `docs/**/archive/`;
  `docs/ru/documentation-rules.md`; `README.md:571-640`.
- **Remaining exact scope:** update only current front-door, testing, deployment,
  affected contract and affected Admin/Simulator architecture documents after the
  owning behavior is accepted. Correct the known Vitest/canonical-verifier drift;
  label translation lag and archives. Do not reconcile every historical body or
  translate stable-but-unchanged content during this renovation.
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

## REN-016 — Move runtime artifacts out of the root and classify remaining surfaces

- **Priority:** P2, required for reproducible normal workflows; cleanup cosmetics
  are SHOULD.
- **Owner surface:** `app/config.py`, local run scripts, `scripts/verify_local.ps1`,
  `pytest.ini`, `tests/conftest.py`, `.gitignore`, current runtime/testing docs,
  `simulator-ui/v1/`, generated fixture entrypoints and proven Admin starter files.
- **Status:** PLANNED (Phase 6).
- **Rationale / value:** Canonical commands and defaults still create or reference
  mutable DB/log/PID/test output in the repository root, contradicting the current
  repository policy and making parallel/repeated work less predictable. The same
  slice records legacy/generated boundaries without deleting history.
- **Exact scope:**
  1. Inventory each canonical producer of root DB, log, PID, NDJSON and test output.
  2. Move defaults below one documented ignored runtime root such as `.local-run/`,
     preserving explicit path overrides.
  3. Document a manual path override/migration for an existing `geov0.db`; never
     move or delete a user DB automatically.
  4. Re-run REN-013A's two-task-slug isolation proof after relocation.
  5. Mark `simulator-ui/v1` read-only historical/reference and exclude it from
     normal gates; physical deletion is not required.
  6. Validate canonical generated fixture sync with no unexplained diff.
  7. In separate SHOULD micro-commits, remove only reference-proven Admin starter
     remnants (`HelloWorld.vue`, `vue.svg`, favicon/title) or newly proven tracked
     runtime artifacts.
- **Non-scope:** deleting a user DB, deleting v1/archives, consolidating Dockerfiles,
  changing fixture schemas, broad top-level reorganization or aesthetic cleanup.
- **Dependencies:** REN-013A resource isolation; product behavior Phases 2–5 must be
  stable before changing their canonical output locations.
- **Acceptance criteria:**
  - A clean canonical run/test/build writes mutable runtime/test artifacts only
    below the documented ignored runtime root.
  - Explicit DB/output overrides still work; existing root `geov0.db` is untouched.
  - REN-013A isolation proof still passes after relocation.
  - v1, generated sources/copies and active v2 have unambiguous classifications.
  - Every deletion has import/runtime/docs/history evidence and an independent
    reversible commit.
- **Targeted gates:** PowerShell/script syntax; affected config/path tests; fixture
  sync/validate no-diff; reference scan; two-task-slug isolation rerun.
- **Full gates:** `BACKEND-DEFAULT`; `ADMIN-UNIT`; `ADMIN-BUILD` when Admin starter
  files change; `SIM-TYPECHECK`/`SIM-UNIT`/`SIM-BUILD` when Simulator path or
  fixture entrypoints change; `git diff --check` and clean normal-workflow status.

## REN-015 — Final adversarial review and renovation closeout

- **Priority:** P1
- **Owner surface:** all changed surfaces, spec changelog, accepted-debt register
- **Status:** PLANNED
- **Rationale / value:** Verify effects across sibling paths and catch regressions
  introduced by the renovation itself before declaring the old-code cleanup done.
- **Evidence paths:** diffs and acceptance evidence for REN-001 through REN-014 and
  REN-016;
  `git log` for each owner surface; CI artifacts; spec changelog and known-open
  decisions.
- **Exact scope:** review only the frozen plan's changed product surfaces and their
  direct callers/contracts, then run the bounded evidence matrix in Phase 7 of
  `plan.md`. This is a falsification pass for shipped changes, not a second
  repository-wide redesign. Each changed slice receives internal review; each
  high-risk range defined in `spec.md` additionally receives Claude Code Opus 5 /
  High review with manual finding disposition.
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
  - The bounded canonical/CI/PostgreSQL/Chromium/container matrix passes. An
    unavailable required environment leaves REN-015 open; removing it requires an
    explicit owner-approved spec revision.
  - Current docs and spec changelog reference the actual merge SHAs and fresh gate
    results.
  - No secret, user artifact, temporary database, test output, or generated bundle
    is newly tracked.
- **Targeted gates:** rerun each changed task's targeted gates and adversarial
  reproductions.
- **Full gates:** `REPO-FULL`; published required workflow; selected disposable
  PostgreSQL concurrency/migration jobs; one production-like container smoke;
  scoped Admin/Simulator Chromium flows; clean-tree and tracked-artifact audit.

## Dependency summary

```text
owner-approved plan
  → REN-003/005/006/008/013A runtime evidence
  → REN-010A backend ownership and bounded PostgreSQL proof
  → REN-009 selected REST/SSE contracts
  → REN-011 + REN-010B Admin operator paths
  → REN-012B/012C Simulator v2 paths (REN-012A only if triggered)
  → REN-013B/014/016 bounded cleanup and current docs
  → REN-015 closure
```

The diagram is sequencing guidance, not authority to bundle all predecessors into
one PR. Parallel tasks must have non-overlapping write surfaces, and shared
contract files must be assigned to one agent at a time.
