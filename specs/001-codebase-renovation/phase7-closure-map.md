# Phase 7 closure evidence map

- **Authorized start:** 2026-08-09
- **Approved-plan anchor:** `8f271693e7b763856d86fb3c2f579a56938d6fcb`
- **Execution base / accepted Phase 6 closeout:**
  `579ad5769470b0d3bcdb4c59830362a7b9bd73bc`
- **Working branch:** `codex/codebase-renovation-phase7`
- **Status:** LOCAL/INTERNAL COMPLETE; PUBLISHED AND EXTERNAL EVIDENCE PENDING

This ledger bounds REN-015. It is a falsification and evidence pass over the
accepted renovation surfaces and their direct callers/contracts, not authority
for a second repository-wide redesign. Historical phase evidence is useful for
range classification and regression comparison, but only fresh Phase 7 results
close the required local matrix. Published PostgreSQL, Chromium and container
jobs must execute on the final accepted Phase 7 SHA.

## Frozen review boundary

The worktree was clean before the Phase 7 branch was created. The branch points
exactly at the accepted Phase 6 closeout above. The program-wide orientation
range is `8f271693e7b763856d86fb3c2f579a56938d6fcb..579ad5769470b0d3bcdb4c59830362a7b9bd73bc`;
reviewers must use the narrower accepted ranges in the phase ledgers when
attributing a finding:

| Surface | Accepted owner range / evidence boundary |
|---|---|
| Phase 1 gates, runtime, migration and published jobs | approved-plan anchor through merged Phase 1 `31e887fc904ef8060b0c1c9f233957b235ee1aeb` |
| Phase 2 backend ownership/integrity | `0635a651f0ae6f970d82d7d71b7a18071069262d..7d0a8a9ca48cec34cc62e0965cdd6d28825370de` |
| Phase 3 selected REST/SSE contracts | `cf76e096ddaf9b896de161a5a44e208f982484fd..f8517bc90b119a3b156de0a2945019d1b9381118` |
| Phase 4 Admin operator paths | `1953845eef960352764c77778e1670456c58f5bf..f0428f9996e511e723ce1980a24c2073b3adadb8` |
| Phase 5 Simulator event/UI paths | `5ea8bd854e25075634edb4436359f73dcb22c704..ff53dbc1c070220ad7ddfdaf8a0aa5c8c1ccd157`, with test evidence at `345991b47f15e2ed4080c7617f4f3430883f8b7b` |
| Phase 6 runtime/repository/docs cleanup | `d6e3e094d14422e20075a70839b0b288efe51bce..90ac5ef6197c184b5818fe52826a8e97dbb5f6d5`, closeout at the execution base |

Protected `simulator-ui/v1`, archives, applied migrations and generated/public
copies remain read-only unless a confirmed in-scope P1/P2 requires an explicit
owner decision. No publication, push or `workflow_dispatch` is authorized by the
Phase 7 start alone.

## Accepted local boundary

The final product/runtime commit tested by the full local matrix is
`5c9a809a6fa5bc726869098f4f94380fa4cf1702`. Commit `f1b6eae` changes only five
active documentation files after the repository/docs review; the following
closure-ledger commit changes only this evidence file. Published jobs must still
run on the eventual branch HEAD and therefore remain the final SHA authority.

`PASS` below means an exact command exited `0`; configured, skipped, cancelled
and collect-only work is not counted as execution evidence.

## MUST evidence matrix

| # | Required evidence | Result |
|---:|---|---|
| 1 | clean status plus tracked secret/artifact scan | **PASS locally** — clean tree before docs closeout; 1,176 tracked files, 0 tracked runtime artifacts, 0 high-confidence secret-file matches, origin user-info false |
| 2 | canonical backend default gate | **PASS locally** — `phase7_final_sha_backend`: 907 passed, 3 skipped, 15 deselected, exit 0 |
| 3 | selected live PostgreSQL migration/concurrency gate | **UNAUTHORIZED / NOT RUN** — published PostgreSQL job required |
| 4 | Admin lint/unit/build plus scoped Chromium smoke | **LOCAL PASS / CHROMIUM PENDING** — 199 tests; lint 0 errors/116 baseline warnings; typecheck/build exit 0 |
| 5 | Simulator lint/typecheck/unit/build plus scoped Chromium smoke | **LOCAL PASS / CHROMIUM PENDING** — 729 tests; lint/typecheck/build exit 0 |
| 6 | production-like image boot/readiness/stop | **UNAUTHORIZED / NOT RUN** — published container job required |
| 7 | OpenAPI plus selected SSE consumer contract tests | **PASS locally** — OpenAPI 23 passed; strict replay 410 1 passed; both exit 0 |
| 8 | changed documentation links and commands | **PASS locally** — 14 changed Markdown files, 0 broken local links; active scenario/artifact/config commands corrected |
| 9 | independent backend, Admin, Simulator and repo/docs reviews | **PASS internally** — findings reproduced, remediated and accepted; no remaining P1/P2 |
| 10 | Claude Code Opus 5 / High evidence for every high-risk product batch | **OWNER AUTHORIZATION PENDING** — no new private diff upload performed |
| 11 | all twelve success criteria linked to passing evidence | **OPEN** — mapping below; published criteria are not waivable |
| 12 | no unresolved in-scope P1/P2; residuals and unverified paths explicit | **PASS internally / external pending** |

The required published workflow, live PostgreSQL, production-like container and
active Chromium milestones remain **UNAUTHORIZED / NOT RUN**. Publication needs
separate authorization for remote `origin`, branch
`codex/codebase-renovation-phase7` and `workflow_dispatch` of
`.github/workflows/quality.yml`.

## Fresh local commands

- `./scripts/verify_local.ps1 -TaskSlug phase7_final_sha_backend -BackendOnly`:
  exit `0`; `907 passed, 3 skipped, 15 deselected` in `467.74s`.
- `npm --prefix admin-ui run test`, `lint`, `build`: exit `0`; `30` files /
  `199` tests; `0` lint errors and `116` existing warnings; fixture validation
  reported `Fixtures OK`; Vue typecheck and Vite build passed.
- `npm --prefix simulator-ui/v2 run lint`, `typecheck`, `test:unit`, `build`:
  exit `0`; `99` files / `729` tests; strict fixture sync produced no diff.
- OpenAPI selector `phase7_final_openapi`: exit `0`, `23 passed`. Strict replay
  selector `phase7_final_sse_replay`: exit `0`, `1 passed`.
- Runtime remediation selector `phase7_runtime_remediation_acceptance`: exit `0`,
  `103 passed`; PowerShell 5.1 and 7 parsed all three launchers with zero errors.
- `git diff --check` on every accepted owner slice exited `0`. The final ledger
  update removes its earlier extra blank line at EOF; final range check is rerun
  after this commit.

## Independent review disposition

| Surface | Result |
|---|---|
| backend recovery/config | Partial-failure rollback, outcome accounting, ENV precedence and credential-safe diagnostics remediated; final targeted selectors passed; no remaining P1/P2 |
| Admin UI | Mock mutation serialization, audit ownership and Graph global/focus/participant request and error ownership remediated; final targeted 47/47 and full 199/199 passed |
| Simulator v2 | Strict run-only replay snapshots, buffered recovery, terminal admission, equivalent/context invalidation and snapshot-before-SSE ordering remediated; final targeted 44/44 and full 729/729 passed |
| runtime launchers | Exact repository/PID/start/listener ownership, mutex exclusion and reverse startup rollback remediated; independent exact-delta review found no P1/P2 |
| repository/docs/CI | Active scenario/config/artifact commands corrected; 0 broken changed-doc links; protected/archive/migration boundaries unchanged; workflow selectors validated |

## External-review inventory

Accepted Phase 2 backend and Phase 3 protected-contract ranges already have
complete Claude Code `2.1.226`, high-effort, resolved `claude-opus-5` evidence in
their phase ledgers. A previously authorized Phase 1 historical rerun also
completed successfully for
`710483fe1e4974b989f5a2258fce319b8880d168..e8d29e04ceeaa32bfc531151ae9f60a2c532fda9`;
its five sustained current findings were remediated in Phase 7.

Additional private-repository uploads were stopped because this session has no
explicit authorization to send those diffs to Claude Code cloud. Historical
gaps and the final Phase 7 security/config/protected-SSE remediation therefore
remain pending. Each authorized run must use a clean credential-free standalone
clone, read-only tools, exact immutable boundaries, complete JSON, exit code and
resolved model evidence; findings receive manual disposition.

## Twelve success criteria

| Criterion | Evidence / state |
|---:|---|
| 1 | Canonical local backend/Admin/Simulator matrix passed; required published workflow **pending**. |
| 2 | Dev launcher configuration, ownership, rollback and secret guardrails passed local tests; production-like image boot/readiness/stop **pending**. |
| 3 | Migration and concurrency selectors exist and validate; fresh disposable PostgreSQL execution **pending**. |
| 4 | Phase 2 owner map plus final backend suite cover selected write paths; live PostgreSQL semantics remain criterion 3. |
| 5 | OpenAPI 23/23, replay 410 1/1, Simulator contract/unit suite 729/729. |
| 6 | Phase 2/3 supervision and overload evidence plus final backend suite passed. |
| 7 | Admin async ownership remediation accepted; full Admin 199/199. |
| 8 | Simulator replay/effect ownership remediation accepted; full Simulator 729/729. |
| 9 | Keyboard/component evidence passed; required scoped Chromium smoke **pending**. |
| 10 | Runtime outputs remain below `.local-run`; tracked artifact scan 0; protected Simulator v1 unchanged. |
| 11 | Current RU config/deployment/Simulator docs corrected; changed-doc links 0 broken. |
| 12 | Internal adversarial reviews have no unresolved P1/P2; required Claude evidence **pending authorization**. |

## Residual and unverified register

- Required published workflow, PostgreSQL, production-like container and Admin/
  Simulator Chromium jobs have not run; REN-015 and the renovation program remain
  open until they pass or the owner explicitly changes the specification.
- Required Claude Code cloud review gaps remain pending explicit permission to
  upload the named private-repository diff ranges. No upload was attempted after
  the authorization boundary was identified.
- `run_real_simulator start` can recreate a running Compose baseline container
  through `up --build`; rollback restores running membership, not the prior
  image/config, and seed changes are not launcher-transactional (accepted P3).
- Backend recovery has no fresh PostgreSQL concurrency proof; concurrent replicas
  may overcount outcomes, and a phase-level DB failure can defer later recovery
  until the next pass (recorded lower-priority limitations).
- Simulator accepts lower unseen event IDs after dedup history pruning; canonical
  backend ordering makes this a noncanonical-input limitation. No real browser or
  live backend SSE timing run was performed locally.
- The outside-scope scenario-upload traversal finding and the lower-priority
  payment commit-cache residual remain deferred; neither was broadened into this
  closure slice.
