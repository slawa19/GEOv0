# Phase 7 closure evidence map

- **Authorized start:** 2026-08-09
- **Approved-plan anchor:** `8f271693e7b763856d86fb3c2f579a56938d6fcb`
- **Execution base / accepted Phase 6 closeout:**
  `579ad5769470b0d3bcdb4c59830362a7b9bd73bc`
- **Accepted product/test HEAD:**
  `f5a86ae369c563ae32d2b306ada3e6b8f48f21e2`
- **Working branch:** `codex/codebase-renovation-phase7`
- **Status:** COMPLETE (2026-08-09)

REN-015 was a bounded falsification pass over the accepted renovation surfaces
and their direct callers/contracts. It did not authorize a second repository-wide
redesign. Code, runtime behavior, tests, git history, published jobs and external
reviews were reconciled before closure.

## Accepted boundary and publication

The Phase 7 product/test range is
`579ad5769470b0d3bcdb4c59830362a7b9bd73bc..f5a86ae369c563ae32d2b306ada3e6b8f48f21e2`.
Protected `simulator-ui/v1`, archives, applied migrations and generated/public
fixture copies were not modified by the final remediation batches.

The canonical published evidence is
[Quality run 31329706963](https://github.com/slawa19/GEOv0/actions/runs/31329706963),
manually dispatched on exact HEAD
`f5a86ae369c563ae32d2b306ada3e6b8f48f21e2`. Its overall conclusion and all
eight jobs are `success`. The earlier run
[31329404040](https://github.com/slawa19/GEOv0/actions/runs/31329404040)
is retained as negative evidence: it exposed an incorrect PostgreSQL test-engine
isolation assumption, which was corrected before the accepting run.

## MUST evidence matrix

| # | Required evidence | Accepted result |
|---:|---|---|
| 1 | clean status plus tracked secret/artifact scan | **PASS** — 1,176 tracked files; 0 tracked runtime artifacts; only three intentional `.env*.example` secret-name matches; origin contains no user-info |
| 2 | canonical backend default gate | **PASS** — local `phase7_final_backend_684e8eb`: 932 passed, 3 skipped, 15 deselected; published required job: 933 passed, 2 skipped, 15 deselected |
| 3 | selected live PostgreSQL migration/concurrency gate | **PASS** — disposable migrated PostgreSQL: concurrency matrix 3/3 and registered marker tier 11/11 |
| 4 | Admin lint/unit/build plus scoped Chromium | **PASS** — local lint 0 errors/116 registered warnings; 200/200 unit tests and build; Admin E2E 7/7; active Chromium smoke passed |
| 5 | Simulator lint/typecheck/unit/build plus scoped Chromium | **PASS** — local lint, typecheck, 729/729 unit tests and build; visual E2E 24/24; active Chromium smoke passed |
| 6 | production-like image boot/readiness/stop | **PASS** — image build, empty-schema boot, restart, `016` upgrade, readiness and graceful cleanup passed |
| 7 | OpenAPI plus selected SSE consumer contracts | **PASS** — OpenAPI 23/23, strict replay selector 1/1, published required and Simulator suites passed |
| 8 | changed documentation links and commands | **PASS** — active command corrections and changed-document local-link scan passed; final Markdown diff rechecked |
| 9 | independent backend, Admin, Simulator and repo/docs reviews | **PASS** — owner-separated reviews completed; findings manually reproduced and disposed |
| 10 | Claude Code Opus 5 / High for every high-risk batch | **PASS** — all required runs used Claude Code 2.1.226, read-only exact ranges, OS exit 0, complete JSON and resolved `claude-opus-5` |
| 11 | all twelve success criteria linked to evidence | **PASS** — mapping below |
| 12 | no unresolved in-scope P1/P2; residuals explicit | **PASS** — all confirmed in-scope P1/P2 fixed; bounded residual register below |

Ruff and Black remain diagnostics under repository policy. Their published job
completed successfully as a non-blocking diagnostic surface; this does not
promote the known repository-wide findings into required gates.

## Final remediation evidence

Phase 7 reproduced and fixed issues in these bounded owner surfaces:

- runtime launchers: exact PID/listener ownership, rollback ordering, bounded
  startup and credential-safe database diagnostics;
- Admin: UTC incident parsing and selection ownership for stale graph results;
- scenario upload: bounded validation errors without changing the canonical
  fixture contract;
- payment/recovery: transaction-lock reacquisition after rollback, recovery
  session rollback, terminal prepare-lock cleanup, SERIALIZABLE `23505` policy,
  cumulative advisory-lock budget and `commit=False` timeout ownership;
- simulator/Admin cancellation: durable outcome resolution, cache invalidation
  for unknown committed observations, and no detached DB task outliving its
  session or runtime-config lock.

Targeted acceptance included runtime 118/118, payment/recovery 38/38, the R1
cancellation/audit/cache group 49/49, R2 lock/retry/timeouts/2PC 35/35, scenario
6/6, incident 3/3, Admin 200/200 and the final full backend result above.
Every accepted batch passed `git diff --check`.

## External review inventory and disposition

Every entry below used a fresh credential-free standalone clone and read-only
tools. `result` JSON was complete with `is_error=false`; CLI was 2.1.226, OS exit
was 0 and `modelUsage` resolved `claude-opus-5`.

| Review boundary | Disposition |
|---|---|
| Phase 1 historical `710483fe1e4974b989f5a2258fce319b8880d168..e8d29e04ceeaa32bfc531151ae9f60a2c532fda9` | five sustained runtime/config findings fixed in Phase 7 |
| Phase 6 runtime `c8edccc8fbda112027bb342f6df6830cbfe0a8f6..90ac5ef6197c184b5818fe52826a8e97dbb5f6d5` | no P1/P2; duplicate mapping/reset guard recorded below P2 |
| Phase 7 synthetic runtime/security `579ad5769470b0d3bcdb4c59830362a7b9bd73bc..ef2e23841fb80f986ffa37fa05d31ed16bbcfa70` and its `97caa9dcd93765e9a9e876063c8a3caa947beded..1b685e0d73fe01d9ae2923c8c8e5f28ff3d8dc66` remediation | startup findings fixed; credential ambiguity then fixed in `aa90836`; no further external loop |
| scenario/equivalent `40f43eea39572a7ec59a0700a5af268bc19344af..032ed86b70eb3b33be53224a6b42949f5d77a052` | bounded error list fixed; explicit repair/manual-cleanup contracts retained |
| Admin `1953845eef960352764c77778e1670456c58f5bf..a7a6be0b6052ae8cd2b25035aa979479f0ab82d2` | UTC and stale-selection findings fixed; one alleged item filter rejected by source trace |
| R1 `e8d29e04ceeaa32bfc531151ae9f60a2c532fda9..d05c27aa7d0ebfc23def9d11c63885e362fc1b4d` | cancellation, cache and audit-publication findings fixed in `dca05e8` |
| R1 remediation `8d0e3efb6f09d7377ce9a56f891af7652af23098..dca05e80316547002dbafac9b703a1eaaeb160a7` | five detach/session/lock findings fixed internally in `b34febe`; policy forbids another external loop |
| R2 `d35bc026bee13ea8510a2408c5d7c244b2d60edb..758bb319c550ace0b523eebc37d0e1517a0205b9` | retry lock loss, SERIALIZABLE duplicate commit, unbounded advisory wait and terminal orphan locks fixed |
| config/recovery synthetic `579ad5769470b0d3bcdb4c59830362a7b9bd73bc..ce304145204150a4a54ca34b2377c545a9b23a68` | poisoned recovery session fixed; canonical `ENV` precedence retained as intended behavior |
| payment remediation `5753ad3d82c8901254217e11838a0504004dfcbb..8d0e3efb6f09d7377ce9a56f891af7652af23098` | no findings |
| R2 remediation `dca05e80316547002dbafac9b703a1eaaeb160a7..0c9b39f1a4f6007a15e27a30876a87534dd89748` | three retry/timeout findings fixed internally in `684e8eb`; policy forbids another external loop |

Synthetic review commits are isolated review-only commits, not refs in the
product repository; their full immutable SHAs above are recorded in the complete
external artifacts.

## Twelve success criteria

| Criterion | Closing evidence |
|---:|---|
| 1 | canonical local gate and all eight published Quality jobs passed |
| 2 | launcher ownership tests plus production-like image boot/restart/stop passed |
| 3 | migrated disposable PostgreSQL concurrency 3/3 and marker tier 11/11 passed |
| 4 | full backend and payment/integrity owner selectors passed |
| 5 | OpenAPI, strict replay, Simulator unit and visual E2E suites passed |
| 6 | bounded supervision/cancellation and overload paths passed in backend suites |
| 7 | Admin async/audit/request ownership passed 200 unit and 7 E2E tests |
| 8 | Simulator replay/effect ownership passed 729 unit and 24 E2E tests |
| 9 | component/keyboard coverage plus both active Chromium smoke paths passed |
| 10 | tracked artifact/secret scan clean; runtime outputs remain under `.local-run`; v1 unchanged |
| 11 | current RU deployment/config/Simulator docs and changed links were verified |
| 12 | internal and required external reviews are complete; no in-scope P1/P2 remains |

## Residual register

- A reverse-direction sibling segment-lock concern is a P2 outside the frozen
  reviewed payment range. It requires a separately approved payment design slice,
  not an unbounded Phase 7 expansion.
- Persisted scenarios with noncanonical equivalent codes remain fail-closed and
  require explicit operator repair/manual cleanup. This is an accepted P3 legacy
  data limitation; generated and new scenarios use canonical codes.
- Launcher rollback restores the prior running membership, not an old image/config
  snapshot; seed changes are not launcher-transactional. This is an accepted P3
  limitation of the single-node development topology.
- The canonical `ENV` value intentionally outranks an unsupported ambient legacy
  `ENVIRONMENT` alias. Conflicting supported values still fail closed.
- Ruff/Black findings and GitHub's Node-action deprecation notices remain
  diagnostics, not regressions introduced by Phase 7.

No required environment is unverified, and no confirmed P1/P2 remains within the
frozen Phase 7 scope. Further renovation requires a new owner-approved
specification tied to a measured product need.
