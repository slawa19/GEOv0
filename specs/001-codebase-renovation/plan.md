# GEOv0 Codebase Renovation Plan

- **Date:** 2026-08-07
- **Specification:** `specs/001-codebase-renovation/spec.md`
- **Executable backlog:** `specs/001-codebase-renovation/tasks.md`
- **State:** owner-approved at exact commit
  `8f271693e7b763856d86fb3c2f579a56938d6fcb`; Phase 1 is current and its exit
  criteria remain open. Later phases remain paused.

## 1. Objective and project fit

The objective is to make the maintained GEOv0 MVP easier to understand, safer to
change and reproducible to run without turning it into a bank-grade or enterprise
platform.

The plan is calibrated to the documented product:

- v0.1-alpha community hub and simulator;
- approximately 10–500 participants in one community;
- one supported application/Compose topology;
- SQLite for local/fast feedback and PostgreSQL for the small set of semantics
  that actually depend on PostgreSQL;
- Admin UI and Simulator UI v2 in Chromium as the active frontend surfaces;
- `simulator-ui/v1` as a legacy/reference surface, not an active refactoring
  target.

Correctness still matters for payments, trustlines, clearing, ownership and audit.
The proportional target is prevention of ordinary double application, lost update,
partial commit, stale UI state and contract drift on supported paths. It is not
formal Byzantine resilience, multi-region failover, exhaustive chaos testing or a
compliance certification.

## 2. Scope freeze and value filter

No product-code implementation begins before owner approval of this plan. After
approval, the task set below is frozen.

A finding enters implementation only when at least one is true:

1. it reproduces a defect in a supported user/operator flow;
2. it threatens a named payment/trustline/clearing/ownership/audit invariant;
3. it makes the canonical build/test/deploy path unreliable or falsely green;
4. it is repeated policy/ownership duplication whose removal is smaller than
   continuing to maintain it;
5. it is a safe repository cleanup with reference/history evidence.

Priority inside the frozen program:

- **MUST:** required to satisfy the bounded completion contract;
- **SHOULD:** do only when adjacent to a MUST slice and demonstrably cheap;
- **ACCEPTED-NOT-DOING:** intentionally outside this renovation.

P3 style, naming and speculative cleanup never expand a wave. A newly discovered
P2 is mapped to an existing task or recorded as residual debt. Only a reproduced
P1 affecting a supported flow can interrupt the sequence.

## 3. Explicit non-goals

The renovation will not implement or prove:

- active-active/multi-region operation, automatic failover or formal RPO/RTO;
- bank-grade settlement, exhaustive commit-ack/network-partition simulation or
  every theoretical concurrency interleaving;
- RBAC/SSO, enterprise session management or a complete endpoint permission
  redesign;
- a new UoW/repository/event-sourcing/outbox framework;
- a generated frontend API client or a new global data/state framework;
- complete decomposition of large files or any LOC target;
- complete WCAG certification, screen-reader/browser/device matrix or visual
  redesign;
- formal performance/load certification without a measured regression;
- a coverage-percentage target, wholesale test rewrites or conversion of every
  source-text test;
- deletion of archives, applied migrations or Simulator v1 merely because they
  are old;
- rewriting every translation or historical document.

## 4. Review method before a change

Each owner slice is reviewed from the product path down to the relevant blocks:

1. supported actor and observable workflow;
2. entrypoint and current result;
3. state/transaction/lifecycle owner;
4. calls across API, application/domain, persistence, events and frontend;
5. success, meaningful rejection, failure, cancellation/rollback and cleanup;
6. existing tests and the smallest missing behavioral proof;
7. Current / Intended / Optimal decision;
8. minimal reversible change or explicit `KEEP` decision.

The compact review record is:

```text
surface / workflow:
current behavior and evidence:
intended behavior and evidence:
optimal MVP target:
owner and callers:
state / side effects / invariant:
confirmed risk:
decision: FIX | KEEP | DELETE | DEFER
tests before / after:
unverified paths:
```

The record is required for changed high-risk modules, not for every file in the
repository.

## 5. Work already delivered, pending final program closure

Earlier implementation began before this plan was owner-approved. It is retained
as evidence but does not authorize more implementation.

- Governance, token/history audit and narrow tracked-artifact cleanup are complete.
- Canonical verification/CI configuration, deployment/config guardrails, ORM
  parity, background-job supervision, semantic OpenAPI, Admin async ownership,
  payment locking/state/error handling and Simulator transaction-outcome work are
  implemented in bounded slices.
- Local backend, Admin and Simulator gates have substantial passing evidence.
- Published CI, Docker runtime, live PostgreSQL, browser E2E and full frontend
  architecture/functionality evidence are still absent.
- Claude Code reviewed frozen product diffs, not the full repository or this final
  plan. Its confirmed product findings were remediated; the plan itself receives a
  separate review before owner presentation.

## 6. Frozen remaining sequence

The phases below are sequential where they share contracts. Read-only inventory
may run in parallel. Product edits from later phases do not begin while an earlier
contract decision is unresolved.

### Phase 0 — Finalize and approve the plan

**Status:** COMPLETE (2026-08-07). The repository owner approved exact commit
`8f271693e7b763856d86fb3c2f579a56938d6fcb`; the approval is recorded in
`spec.md`.

#### MUST

1. Reconcile `spec.md`, this plan and `tasks.md` around the MVP proportionality
   boundary.
2. Give every remaining acceptance sub-slice exactly one owning phase; parent
   `REN-*` records spanning several surfaces close only after all named sub-slices.
3. Record MUST, SHOULD and ACCEPTED-NOT-DOING decisions.
4. Obtain a read-only internal adversarial review.
5. Commit the coherent plan-only batch.
6. Review that exact batch through Claude Code with `--model opus --effort high`;
   verify the resolved Opus 5 model in JSON and manually assess every finding.
7. Present the corrected sequence to the owner and wait for explicit approval.
8. Before Phase 1 needs published evidence, obtain a separate authorization naming
   the Git remote, branch and `workflow_dispatch` trigger. Plan approval alone does
   not authorize a push, deployment or mutation of external data.

#### Exit

- The three planning files agree.
- No product/runtime/test implementation file changed in this phase.
- Internal and Claude findings are either incorporated or rejected with evidence.
- Owner approval is the only transition to Phase 1. It is recorded as a dated
  `spec.md` changelog entry naming the approving owner and exact approved plan
  commit SHA; a status label or chat inference alone is not sufficient.

### Phase 1 — Make the existing gates and supported runtime truthful

**Status:** current phase; exit criteria open. Work is limited to the frozen
Phase 1 scope below. Git push and `workflow_dispatch` remain unauthorized until
the separate owner authorization required by Phase 0 item 8 is recorded.

**Purpose:** establish reliable evidence before further behavioral refactoring.

**Maps to:** remaining parts of REN-003, REN-005, REN-006, REN-008 and REN-013A.

#### 1A. Canonical gate completion — MUST

Owner surfaces:

- `.github/workflows/quality.yml`;
- `scripts/verify_local.ps1`;
- backend test configuration;
- Admin and Simulator package/config scripts.

Work:

1. Run the required workflow on the published branch and retain actual job results.
2. Keep backend Ruff/Black debt diagnostic unless a small changed-files ratchet can
   be made deterministic; do not reformat the repository.
3. Add a Simulator lint command only as a correctness-focused ratchet for active
   `src`; baseline style debt remains non-blocking and registered.
4. Define one short non-visual Chromium smoke for each active UI. Expensive visual
   and real-runtime suites remain scheduled/manual milestones.
5. Prove task slugs isolate DB, basetemp and artifacts for two verifier invocations;
   full `xdist` adoption is not required.

Accepted published evidence is a completed `workflow_dispatch` run with retained
run URL and logs. The existing `postgres`, `admin-e2e` and relevant Simulator jobs
may satisfy PostgreSQL/Chromium milestones when they execute the exact frozen
selectors; local absence of PostgreSQL or Chromium is not a substitute and not a
blocker if the published job completes.

Gates:

- canonical backend default selector;
- Admin lint, unit, typecheck/build;
- Simulator lint ratchet, typecheck, unit, build;
- workflow syntax plus one completed published workflow.

Stop:

- no coverage target;
- no mass lint cleanup;
- no new CI matrix beyond Windows/current Python/Node and the selected PostgreSQL
  job unless an existing supported platform requires it.

#### 1B. Production-like boot and schema proof — MUST

Owner surfaces:

- `.github/workflows/quality.yml`;
- `Dockerfile`, `docker/Dockerfile`, Compose files and entrypoint;
- `app/config.py`, `app/main.py`, health/readiness;
- Alembic and Simulator owner migration/model.

Work:

1. Add a `workflow_dispatch` container-smoke job that builds the selected image,
   starts it against disposable PostgreSQL with non-placeholder secrets, checks
   readiness, and verifies graceful stop. This published job is the canonical path
   while local Docker is unavailable.
2. Document which current image/entrypoint is production-like and which is the dev
   path. Consolidate files only if runtime evidence shows conflicting behavior;
   duplication alone is not a rewrite mandate.
3. Boot the selected image against an empty disposable PostgreSQL DB with explicit
   non-dev secrets and verify migration, liveness, readiness and graceful stop.
4. Extend the published PostgreSQL/container path with a 016→head fixture/step (or
   name an owner-provisioned local Docker/PostgreSQL environment) and compare the
   affected Simulator owner nullability/index metadata.
5. Start the same topology again and prove migrations/startup are repeatable.
6. Record a forward-fix procedure; do not build formal rollback automation unless
   the current deployment contract promises downgrade.

Gates:

- container build/boot/health/stop exit codes;
- Alembic single-head check;
- empty DB and 016→head migration selectors;
- affected ORM↔PostgreSQL metadata check.

Stop:

- one app instance and one disposable PostgreSQL instance are sufficient;
- no replica-race, failover or backup/restore exercise in this renovation.

#### Phase 1 exit

- The published and local gates report their real status.
- One supported production-like path boots and stops.
- Required runtime outputs are isolated or have an explicit Phase 6 producer fix.
- No product behavior refactor has relied only on a false-green environment.

### Phase 2 — Close only confirmed backend ownership and integrity gaps

**Purpose:** finish the domain review that has broad correctness value without
turning the MVP into a distributed transaction research project.

**Maps to:** REN-010A and conditional REN-012A.

#### 2A. Current transaction-owner map — MUST, review first

Trace these supported paths:

1. public payment create/execute and idempotent repeat;
2. clearing preview/execute;
3. trustline create/update/close used by the active clients;
4. Admin participant/equivalent/transaction mutation plus audit;
5. destructive Integrity repair operations plus their audit/cache/publication;
6. Simulator actions that call payment/clearing/trustline services.

For each, record caller → transaction owner → flush/commit/rollback → event/cache/
audit publication. Mark `FIX` only where ownership is split or an effect can become
visible before the durable outcome. A large service that has one clear owner may
remain large.

The review must explicitly confirm or reject three existing high-value hypotheses:

- successful trustline create/update/close may leave the payment topology/capacity
  cache stale;
- trustline audit/checkpoint failures may be swallowed after a mutation commits,
  creating a weaker audit promise than the Admin/operator flow implies;
- an unexpected clearing failure may be collapsed into the same `None` result as an
  ordinary no-cycle/policy skip, hiding an operational defect.

These are investigation targets, not pre-approved rewrites. A failing behavioral
test or traced observable contradiction is required before code changes.

#### 2B. Minimal UoW remediation — MUST only for confirmed findings

Rules:

- one transaction owner per changed use case;
- preserve existing public facades and wire responses;
- do not introduce a generalized repository/UoW framework;
- keep audit in the same transaction when the operation promises durable audit;
  otherwise make best-effort semantics explicit rather than pretending atomicity;
- type only the error/outcome distinctions that callers actually need;
- change one use-case seam per commit.

Required behavioral cases for each changed path:

- one normal success;
- one meaningful domain rejection;
- database/internal failure before commit;
- cancellation/rollback where the path already supports cancellation;
- idempotent repeat for payment;
- no visible cache/event/audit side effect after rollback.

The two maintained Integrity repair operations receive an explicit `FIX` or `KEEP`
decision with normal success, pre-commit failure/rollback and audit/cache/
publication evidence. This does not authorize new repair algorithms or a shared
UoW abstraction.

For a confirmed trustline cache defect, prefer post-commit invalidation through the
existing cache owner (or disabling that cache when already configured with zero
TTL). Do not add distributed cache invalidation for the one-instance target. For a
confirmed clearing taxonomy defect, preserve expected no-cycle/lock/policy skips
but surface a sanitized unexpected failure through the existing API/log/metric
path; do not redesign the clearing algorithm.

#### 2C. Selected live PostgreSQL concurrency — MUST

Use a disposable DB and run only this bounded matrix:

1. two payments competing for the same constrained capacity;
2. payment versus clearing on the same affected trustline;
3. duplicate payment `tx_id` requests;
4. one changed Admin mutation race if the owner map confirms a lost-update risk.

The invariant is no double application, capacity violation, regressing terminal
state or partial audit/publication. Process crash, network partition after commit,
lease-fencing fleets and exhaustive schedules are ACCEPTED-NOT-DOING.

#### 2D. Basic actor/owner matrix — MUST, evidence-led

Inventory the active route dependencies for anonymous, active/inactive participant,
Admin token and anonymous Simulator cookie/CSRF owner. Reuse existing tests and add
only missing rows for cross-owner access and the supported one-time challenge or
refresh path. Fix a replay/concurrent-consume race only if it reproduces against
the production-like store. Do not add RBAC, SSO, MFA, device management or a new
session platform.

#### 2E. Simulator backend boundary — SHOULD, conditional

Do not decompose the 2,600-line router as a goal. Extract only a route-independent
application/lifecycle adapter required by a confirmed Phase 3/5 contract or by a
repeated transaction-owner problem. Stop after callers have one stable facade and
the changed behavior is independently testable.

#### Phase 2 exit

- Every listed path has a compact owner map and a `FIX` or `KEEP` decision.
- Confirmed ambiguous boundaries are corrected and targeted tests pass.
- The four-case PostgreSQL matrix completes successfully. If the environment is
  unavailable, Phase 2 remains open; changing that requirement needs an explicit
  owner-approved spec revision.
- No new framework or broad module rewrite was introduced.

#### Conditional operational follow-up — not a renovation blocker by default

Before storing real community data, rehearse one `pg_dump` custom-format backup and
restore into a new disposable DB, then verify Alembic head, representative table
counts and the existing integrity check. For local/demo use this is
`ACCEPTED-NOT-DOING`; no PITR/WAL/cloud backup or RPO/RTO program is required.

### Phase 3 — Complete the backend-to-frontend contract chain

**Purpose:** validate only the contracts used by the frozen functional matrix.

**Maps to:** remaining REN-009 and REN-012B1.

#### 3A. REST/Admin contracts — MUST

1. Keep `api/openapi.yaml` and FastAPI semantic parity for maintained operations.
2. Add shared real/mock validation only for Admin config/feature flags, participant
   mutation, transaction abort, equivalent mutation and integrity actions.
3. Fix feature-flag PATCH to send the actual partial change rather than a stale
   GET-plus-full-state write; prove two independent updates do not overwrite one
   another.
4. Reject malformed critical responses with one explicit contract error path.

Stop after these operator-significant payloads. Health/dashboard presentation blobs
do not need exhaustive schemas unless they control a mutation or readiness decision.

#### 3B. Simulator REST/SSE ingress — MUST

1. Validate critical real-mode responses: scenario list/detail, run status and
   snapshot.
2. Define malformed/unknown SSE behavior: diagnose and ignore/recover without
   mutating trusted state.
3. Characterize replay cursor, duplicate event, stale run id and `410` full-refresh
   behavior before extraction.
4. Create captured producer→decoder cases for the event families used in Phase 5:
   lifecycle/status, topology, payment and clearing.
5. Do not version or redesign the wire format unless Current / Intended / Optimal
   evidence shows an actual incompatibility.

#### Phase 3 exit

- Intentional drift of a selected REST payload or SSE event breaks a contract test.
- Real and mock Admin clients use the same selected decoders.
- Unknown/malformed Simulator input cannot silently become a trusted event.
- OpenAPI and affected frontend gates pass.

### Phase 4 — Finish the Admin UI on actual operator paths

**Purpose:** close the async and operator correctness already started, without UI
redesign or a new data-fetching architecture.

**Maps to:** REN-011 plus REN-010B.

#### 4A. Async ownership proof — MUST

Scope only confirmed overlapping loaders:

- Participants, Trustlines, Audit Log, Incidents, Equivalents, Liquidity;
- Graph snapshot/focus/cycle/analytics/rebuild.

Work:

1. Inventory each loader as `overlapping`, `one-shot` or `write`.
2. For overlapping owners, mount the actual page/composable and resolve request B
   before A; A may not overwrite B data/error/loading/URL state.
3. Unmount with pending request/debounce/throttle and prove no late router/state/
   graph effect.
4. Reuse the existing generation-token approach. AbortController is optional and
   no global query framework is introduced.

#### 4B. Operator workflow matrix — MUST

One representative success and one meaningful failure per class are sufficient:

1. Participants: filter/detail → freeze/unfreeze → visible result/audit link;
2. Config/flags: load → edit → save/rejection → durable visible result;
3. Incidents: inspect → abort with reason → terminal result/audit;
4. Integrity: status → verify; repair only for confirm/read-only/failure semantics;
5. Equivalents: one create/update/state-change path with usage guard;
6. Read-only investigation: Graph/Trustlines/Liquidity/Audit navigation preserves
   relevant query/filter state.

Do not create a combinatorial CRUD matrix. Backend transactional proof remains in
Phase 2 and is not inferred from mock UI success.

#### 4C. Graph boundary and practical keyboard path — MUST, bounded

1. Characterize current init/destroy/rebuild/select behavior.
2. Extract at most one narrow Cytoscape lifecycle adapter if it reduces the proven
   mixed ownership; do not decompose analytics/rendering by LOC.
3. Provide a DOM search/list route to select a node or edge and open its existing
   details. Do not implement a complex ARIA canvas-navigation model.
4. Add accessible name, busy/error/selection announcements and focus restoration.
5. Replace dev-only graph tap hooks as evidence for the keyboard user path.

#### 4D. Admin Chromium milestone — MUST

Mock smoke:

- rapid Participants/Trustlines filtering;
- Graph filter, keyboard selection and drawer;
- Config save/rejection and read-only state;
- one query-preserving cross-navigation.

Selected disposable real-contract smoke:

- feature-flag/config change with audit observation;
- participant freeze/unfreeze with cleanup.

Other destructive flows remain component/contract tests unless a deterministic
fixture already exists. Firefox/WebKit and visual redesign are not required.

#### Phase 4 exit

- Confirmed loaders have deterministic reverse-resolution/unmount evidence.
- Selected contracts and operator workflows pass unit/component tests.
- Admin lint/unit/build and scoped Chromium smoke pass.
- Graph has a usable keyboard alternative without a broad Cytoscape rewrite.

### Phase 5 — Make Simulator v2 behavior testable at its risky seam

**Purpose:** separate trusted event/state handling from visual effects only as far
as needed for current real/fixture/interact workflows.

**Maps to:** REN-012B2 and REN-012C.

#### 5A. Characterize the event pipeline — MUST

Record the current ordering from HTTP/SSE input through normalization, replay/
deduplication, state mutation, rendering wakeup and FX. Capture these sequences:

- start, stop, restart and error;
- duplicate event and stale run id;
- reconnect and `410` full refresh;
- topology patch;
- payment success/failure/cancel;
- clearing patch/completion.

Only a supported sequence with observable state/UI effect enters refactoring.

#### 5B. Extract decoder/reducer/effect seam — MUST, incremental

1. Move replay cursor/dedup and accepted-event application behind narrow functions.
2. Make state application independently testable without canvas/time. A reducer may
   mutate an explicitly owned draft if that is simpler; immutability is not a goal.
3. Return/emit small effect intents so FX runs after an accepted state transition
   and never for dropped/stale/malformed events.
4. Migrate one family at a time: lifecycle → topology → payment → clearing.
5. Preserve the current `useSimulatorApp`/`useSimulatorRealMode` facade for callers.

Stop when the four event families are separated and deterministic. The remaining
large composables may stay large; no new state framework is allowed.

#### 5C. Critical Simulator functionality and accessibility — MUST

Functional matrix:

1. fixture bootstrap/switch;
2. real preview plus stale-run recovery;
3. start/stop/restart/error;
4. payment success and one rejection/cancel path;
5. trustline create/edit and blocked close;
6. clearing preview/confirm/result;
7. node/edge inspect.

Accessibility target:

- DOM-based node/edge navigator using existing collections;
- keyboard access to the existing payment/trustline/clearing forms;
- focus entry/restore for changed dialogs/windows;
- busy/error/success announcements;
- FX canvas marked decorative and optional FX disabled under reduced motion.

This is a critical-path improvement, not complete WCAG certification.

#### 5D. Simulator Chromium milestone — MUST

- unit sequence tests for decoder/replay/state/effect ordering;
- component tests for the changed UI/focus semantics;
- short non-visual Chromium smoke for the functional matrix;
- typecheck, unit, build and correctness-focused lint ratchet;
- run existing visual snapshots only if the slice intentionally changes visuals.

#### Phase 5 exit

- Selected malformed/replay sequences are deterministic.
- Trusted state transition precedes optional FX.
- Critical real/fixture/interact paths have unit/component/browser evidence.
- No full composable rewrite, visual redesign or browser matrix occurred.

### Phase 6 — Bounded test, repository and documentation cleanup

**Purpose:** remove false signals and routine dirt after behavior has stabilized.

**Maps to:** REN-013B, REN-014 and REN-016. REN-004 remains complete historical
cleanup evidence and is not reopened.

#### 6A. Test-value cleanup — MUST only where proven

1. Delete a test only when it is pass-only, duplicated by stronger behavior proof
   or tied to a removed contract.
2. Replace source-text assertions only in files touched by Phases 2–5, unless the
   assertion is an explicitly named architecture/policy guard.
3. Split giant test files only while adding changed behavior and only when it makes
   ownership materially clearer.
4. Keep visual baselines platform-scoped; no mass snapshot regeneration.
5. After REN-016 relocates runtime producers, re-run the REN-013A two-task-slug
   isolation check. REN-013A remains the owner of that proof.

#### 6B. Runtime artifact producers — MUST

1. Move canonical DB/log/PID/test outputs below `.local-run/` or another single
   documented ignored runtime root.
2. Preserve explicit path overrides.
3. Provide a manual migration/override note for an existing root `geov0.db`; never
   move or delete a user DB automatically.
4. After producer fixes, inventory old ignored root artifacts and ask for separate
   deletion approval if cleanup would remove user data.

#### 6C. Repository classification — MUST/SHOULD

MUST:

- record `simulator-ui/v1` as read-only historical/reference and exclude it from
  normal gates;
- preserve generated fixture sources/copies and prove sync produces no unexplained
  diff;
- keep archives non-normative.

SHOULD, as separate micro-commits:

- remove proven-unused Admin `HelloWorld.vue`, `vue.svg`, favicon/title remnants;
- remove any newly proven tracked runtime artifact.

No mandatory v1 deletion, archive deletion or Dockerfile consolidation.

#### 6D. Current documentation only — MUST

Update only the maintained documents affected by accepted behavior:

- root `README.md`;
- `docs/ru/00-overview.md` and `docs/ru/09-decisions-and-defaults.md` when product
  semantics changed;
- current config/deployment/testing documents;
- Admin/Simulator current indexes and affected architecture/contract guide;
- `docs/ru/development-standards.md` stale Vitest/canonical-test commands.

Mark EN/PL lag explicitly rather than translating unstable content. Run link and
command scans for changed current documents, not all historical prose.

#### Phase 6 exit

- Canonical workflows no longer create mutable outputs in repository root.
- Only evidence-backed cleanup occurred.
- v1/generated/archive classifications are unambiguous.
- Current documentation describes the verified commands and changed contracts.

### Phase 7 — Final bounded adversarial review and closure

**Maps to:** REN-015.

#### MUST evidence matrix

1. clean status and tracked secret/artifact scan;
2. canonical backend default gate;
3. selected live PostgreSQL migration/concurrency gate;
4. Admin lint/unit/build plus scoped Chromium smoke;
5. Simulator lint/typecheck/unit/build plus scoped Chromium smoke;
6. production-like image boot/readiness/stop;
7. OpenAPI and selected SSE consumer contract tests;
8. changed documentation links/commands;
9. internal reviewers for backend, Admin, Simulator and repository/docs;
10. Claude Code Opus 5 / High review for each high-risk product batch, with exact
    range, resolved model, full JSON and manual disposition. High-risk triggers
    are payment/clearing/integrity transaction semantics, persistence/migration/
    security behavior and protected REST/SSE contracts. Other UI/cleanup/docs
    batches receive independent internal review.

Published `workflow_dispatch` job URLs/logs are canonical evidence for required
PostgreSQL, Chromium and container milestones when their selectors match this
matrix. Configured-but-unrun jobs and skipped jobs are not evidence.

#### Finding policy

- confirmed in-scope P1/P2: fix in the owning slice and rerun its gates;
- unrelated P1: pause and request explicit rescope;
- P2 outside the frozen scope: residual-debt register;
- P3: record only when useful; never reopen implementation;
- one external remediation review is allowed for a fix delta; no infinite loop.

#### Final exit

- All twelve bounded success criteria in `spec.md` have linked passing evidence.
  Required published-CI, PostgreSQL, container and Chromium evidence cannot close
  as `UNVERIFIED`; changing one requires an explicit owner-approved spec revision.
- No confirmed unresolved P1/P2 remains inside the frozen scope.
- The residual-debt register distinguishes accepted limitations from defects.
- The owner receives a plain-language result and the exact unverified paths.

## 7. Dependency order

```text
Phase 0 plan review + owner approval
  → Phase 1 truthful gates/runtime/PostgreSQL baseline
  → Phase 2 backend ownership and integrity
  → Phase 3 selected REST/SSE contracts
  → Phase 4 Admin operator paths
  → Phase 5 Simulator event/UI paths
  → Phase 6 tests/repository/docs cleanup
  → Phase 7 final bounded review
```

Admin read-only characterization may run alongside Phase 2. Simulator read-only
sequence capture may run alongside Phases 2–3. Contract-changing edits remain
sequential. Cleanup that touches active entrypoints waits for the owning phase.

## 8. Batch and independent-review protocol

Each implementation batch has one owner surface, one behavior reason and one
rollback commit. The orchestrator verifies the diff, call sites and canonical
targeted gates before accepting an agent result.

High-risk product batches receive:

1. read-only adversarial review by a different internal agent;
2. a coherent commit range;
3. Claude Code review from a standalone credential-free clone using:

```powershell
claude.exe -p --model opus --effort high `
  "/code-review high <BASE>..<HEAD>" `
  --permission-mode plan `
  --disallowedTools "Edit,Write,NotebookEdit" `
  --output-format json
```

The resolved model must be `claude-opus-5`; exit code, complete JSON and stderr are
retained outside the repository. Claude findings are evidence, not instructions:
the orchestrator reproduces P1/P2 before any remediation.

High-risk means a batch that changes payment/clearing/integrity transaction
semantics, persistence/migration/security behavior or a protected REST/SSE
contract. Other bounded UI, test, cleanup and documentation batches receive an
independent internal review; they are not sent to the cloud by default.

Planning/governance-only batches use the same Opus 5 / High session with an
explicit read-only prompt to assess proportionality, sequencing, missing
dependencies, unverifiable exit criteria and overengineering. `ultrareview` is not
used for governance/spec-only changes.

## 9. Stop rule

The renovation stops after Phase 7. Large files, old tests, additional accessibility
polish, performance ideas and architectural preferences remaining at that point do
not justify continuation without a new owner-approved specification tied to a
measured product need.
