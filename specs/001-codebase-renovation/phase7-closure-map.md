# Phase 7 closure evidence map

- **Authorized start:** 2026-08-09
- **Approved-plan anchor:** `8f271693e7b763856d86fb3c2f579a56938d6fcb`
- **Execution base / accepted Phase 6 closeout:**
  `579ad5769470b0d3bcdb4c59830362a7b9bd73bc`
- **Working branch:** `codex/codebase-renovation-phase7`
- **Status:** IN PROGRESS

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

## MUST evidence matrix

`PASS` requires the exact command/run, exit code, selector/count where relevant,
and final accepted SHA. Configured, skipped, cancelled and collect-only work is
not execution evidence.

| # | Required evidence | Initial status |
|---:|---|---|
| 1 | clean status plus tracked secret/artifact scan | PENDING |
| 2 | canonical backend default gate | PENDING |
| 3 | selected live PostgreSQL migration/concurrency gate | PENDING |
| 4 | Admin lint/unit/build plus scoped Chromium smoke | PENDING |
| 5 | Simulator lint/typecheck/unit/build plus scoped Chromium smoke | PENDING |
| 6 | production-like image boot/readiness/stop | PENDING |
| 7 | OpenAPI plus selected SSE consumer contract tests | PENDING |
| 8 | changed documentation links and commands | PENDING |
| 9 | independent backend, Admin, Simulator and repo/docs reviews | IN PROGRESS |
| 10 | Claude Code Opus 5 / High evidence for every high-risk product batch | INVENTORY IN PROGRESS |
| 11 | all twelve success criteria linked to passing evidence | PENDING |
| 12 | no unresolved in-scope P1/P2; residuals and unverified paths explicit | PENDING |

The four published milestones (required workflow, selected live PostgreSQL,
production-like container and active Chromium) remain **UNAUTHORIZED / NOT RUN**.
Before publication the owner must separately name the remote, branch and
`workflow_dispatch` trigger.

## Review assignments

| Reviewer surface | Boundary | Status |
|---|---|---|
| backend/persistence/contracts | backend owner paths and their protected contracts | RUNNING, read-only |
| Admin UI | changed Admin consumers, loaders, effects and Graph path | RUNNING, read-only |
| Simulator v2 | changed REST/SSE ingress, event/effect and critical UI paths | RUNNING, read-only |
| repository/docs/CI | tracked artifacts, commands, jobs, current docs and classifications | PENDING, read-only |

Every reported finding is independently reproduced before disposition. Confirmed
in-scope P1/P2 is fixed in its owner slice and its gates are rerun; unrelated P1
pauses the phase; outside-scope P2 is recorded as residual debt; P3 does not
reopen implementation.

## External-review inventory

The Phase 2 backend range and the Phase 3 protected-contract product/remediation
ranges already have complete Claude Code `2.1.226`, high-effort,
`claude-opus-5` evidence in their phase ledgers. Phase 1 persistence/security,
late Phase 3 remediation, and Phase 6 runtime deltas are being checked commit by
commit for any high-risk content not included in an accepted external range.
Phase 4/5 UI product batches and Phase 6 docs/cleanup require independent internal
review unless their diff crosses a protected REST/SSE, persistence, migration or
security boundary. Any newly required external run will use a clean standalone
filesystem-origin clone, read-only tools, complete JSON and manual disposition.

## Residual and unverified register

- Fresh live PostgreSQL, production-like container and published Chromium evidence
  are not yet available for the Phase 7 SHA.
- No final secret/artifact scan, full local gate or final documentation command
  scan has run yet.
- Findings from the four independent reviews remain pending.

