# Phase 4 Admin ownership and operator map

- **Date:** 2026-08-08
- **Execution base:** `1953845eef960352764c77778e1670456c58f5bf`
- **Accepted product HEAD:** `f0428f9996e511e723ce1980a24c2073b3adadb8`
- **Scope:** REN-011 plus REN-010B only
- **Status:** COMPLETE locally; no published workflow or “CI green” claim

## Current / Intended / Optimal decision

### Current behavior at the execution base

- The Admin UI already had a narrow generation-token primitive, but the selected
  pages did not all have mounted reverse-resolution and pending-unmount evidence.
  Several write pages also allowed a late completion to mutate page-local state
  after unmount.
- Graph initialization, rebuild and selection behavior was split between the page
  and `useGraphVisualization`; the graph host disappeared while loading, and the
  only practical selection evidence depended on pointer/dev-hook behavior. The
  published Phase 3 Admin E2E baseline was `2 failed / 2 passed`, with both failures
  in `graph.spec.ts`; that baseline was evidence to characterize, not authority to
  rewrite Cytoscape.
- Config JSON rows could become dirty immediately after load because displayed and
  saved representations were compared inconsistently. Some investigation links
  also dropped active query/filter state.
- `AdminAuditLogItem.timestamp` accepted a naive SQLite datetime on the selected
  audit path. The corresponding sibling participant/incident timestamps were not
  confirmed on that operator path.

### Intended behavior

The owner-approved Phase 4 contract is latest-request-wins and no late page/
router/graph effects after unmount for the confirmed overlapping Admin loaders,
plus truthful representative operator success and failure paths, lossless
relevant query navigation, and a usable DOM keyboard alternative for Graph. It
explicitly does not authorize a new query/state framework, combinatorial CRUD
coverage, visual redesign, a complex ARIA canvas model or Phase 5 replay work.

### Optimal bounded target

- Reuse `useLatestRequest`/generation tokens at the existing overlapping page and
  composable owners. Keep writes owned by the backend; add page-local completion
  guards only on the selected write pages where late local effects were confirmed.
- Keep one Cytoscape Core lifecycle owner in the existing
  `useGraphVisualization` seam. Add a bounded, debounced DOM node/edge search that
  opens the existing details drawer; do not rewrite rendering or analytics.
- In guarded large-graph mode, data/filter/locale changes invalidate cached and
  pending keyboard results without an automatic O(N) retained-query rescan. A new
  scan requires explicit remote-search input; the one exception is a deterministic
  false-to-true guard transition, which may scan the current query once.
- Prove representative user-visible contracts with component/unit, scoped mock
  Chromium and a disposable real-contract smoke rather than an exhaustive matrix.

## Loader and effect ownership

| Surface | Classification | Owner and accepted evidence |
|---|---|---|
| Participants list/filter | overlapping | Page generation token; mounted B-before-A, stale rejection and pending-unmount assertions in `adminAsyncOwnership.test.ts`. |
| Trustlines list/filter | overlapping | Existing page generation-token owner retained; mounted rapid-filter/reverse-resolution evidence and Chromium latest-visible-result smoke. |
| Audit Log query/filter | overlapping | Page generation token plus route-hydration guard; raw query is preserved in the URL, while trimmed API ownership prevents redundant whitespace-equivalent loads. Reverse resolution, debounce cancellation and unmount are deterministic. |
| Incidents list/filter | overlapping | Page generation token; stale data/error/loading and late-unmount effects are rejected. |
| Equivalents list/filter | overlapping | Page generation token; stale response and unmount behavior are covered without broad CRUD expansion. |
| Liquidity composite reads | overlapping | One page request generation owns the combined visible result; older composite completion cannot replace the latest state. |
| Graph snapshot/focus | overlapping | `useGraphData` generation owns snapshot/focus publication and loading/error finalization. |
| Graph cycles/analytics | overlapping | Existing analytics generation owners reject stale completion and dispose pending work. |
| Graph rebuild/select | overlapping | Page watcher generation plus the single Core lifecycle owner prevent stale rebuild/selection effects; drawer refresh deliberately preserves viewport. |
| Config/status/usage initial reads | one-shot | Non-overlapping initial reads; representative Config, Integrity and Equivalent usage behavior is tested without claiming a universal unmount guard. |
| Graph Core init/destroy | one-shot lifecycle | `useGraphVisualization` exclusively creates, binds, rebuilds and destroys the Core; the page supplies data and user intent. |
| Freeze/unfreeze, config/flags, abort, integrity verify/repair, Equivalent mutation | write | Backend request is not cancelled as a substitute for transaction ownership. Participants, Incidents and Equivalents have explicit page-lifetime completion guards; Config/flags and Integrity have representative success/failure evidence but no universal unmount claim. Real durability remains the backend contract from Phase 2/3. |

## Representative operator workflows

- **Participants:** freeze and unfreeze success plus meaningful rejection; the
  disposable real smoke observes both audit records and restores the participant.
- **Config/flags:** save success, malformed/local rejection, backend rejection and
  auditor read-only behavior. The real smoke changes a feature flag through the
  canonical Config route, observes `admin.config.patch`, and restores the value.
- **Incidents:** abort success/terminal refresh and rejection, with no late local
  completion after unmount.
- **Integrity:** status/verify success and bounded repair confirm/read-only/failure
  semantics. No new repair capability or backend transaction claim was added.
- **Equivalents:** one create/update/state-change class is covered with usage guard
  and rejection. Invalid legacy codes remain manual cleanup.
- **Investigation navigation:** Graph, Trustlines, Liquidity and Audit preserve the
  relevant query/filter contract; raw Audit search text is lossless in the route
  while its trimmed API value owns reload identity.

`AdminAuditLogItem.timestamp` now attaches UTC only when the database value is
naive. This was changed only after the selected config/flags and participant audit
paths confirmed the mismatch; already-aware timestamps are preserved.

## Graph boundary and keyboard alternative

- `useGraphVisualization` is the single Core lifecycle owner. The page no longer
  destroys/recreates the host during loading and does not introduce a second
  adapter or broad Cytoscape rewrite.
- `GraphKeyboardNavigator.vue` provides a DOM route for node/edge search and opens
  the existing details drawer. The route has an accessible name, busy/error/
  selection announcements and focus restoration after drawer close.
- Small graphs use immediate local options. Oversized graphs use a minimum query,
  debounce and a 100-result bound. Source/filter/locale changes cancel pending
  work and clear stale labels; a combined Vue watcher makes guard activation
  deterministic without depending on watcher declaration order.
- The former dev-only tap path is no longer the browser proof. Scoped Chromium
  selects both a node and an edge through the keyboard route.

## Verification ledger

Final product-state commands unless otherwise noted:

- `npm --prefix admin-ui run lint` — exit `0`; `0` errors and `116` known warnings.
- `npm --prefix admin-ui run test` — exit `0`; `28` files, `154` tests passed.
- `npm --prefix admin-ui run build` — exit `0`; fixture sync/validation,
  `vue-tsc -b` and Vite build passed.
- `$env:PW_E2E_PORT='5197'; npm --prefix admin-ui run test:e2e:phase4` — exit `0`;
  scoped Chromium `5/5` passed and the owned port had zero listeners afterward.
- `.\scripts\verify_admin_phase4_real_contract.ps1 -TaskSlug phase4_orchestrator_real_contract -BackendPort 18141 -UiPort 41741`
  — exit `0`;
  disposable real-contract Chromium `2/2` passed. Owned processes, ports, SQLite,
  WAL and SHM artifacts were cleaned. Later product commits touched Graph only.
- `.\scripts\verify_local.ps1 -TaskSlug phase4_audit_timestamp -BackendOnly -BackendSelector tests/unit/test_admin_audit_log_list.py`
  — exit `0`; `2/2`
  passed. Later product commits did not touch backend schemas.
- `git diff --check` — exit `0` after the final product commit.

The Graph and non-Graph owner slices received independent read-only adversarial
review. Final re-reviews were CLEAN; the Graph reviewer also probed actual Vue
batched-watch tuple semantics for the coalesced guard transition.

Claude Code `2.1.226` reviewed the frozen product and remediation ranges from
clean standalone clones with no origin and no clone-local credential config.
Each accepted run completed with exit `0`, complete JSON, `is_error=false`, high
effort and resolved `claude-opus-5`. Reproduced findings were fixed and re-run.
The final `b9c333da6946210fb375c9583902fbc4c52520ae..f0428f9996e511e723ce1980a24c2073b3adadb8`
review confirmed the deterministic watcher remediation. Its sole remaining note
requested automatic re-search after dependency invalidation; manual triage
rejected that as contrary to the accepted bounded large-graph policy above, not a
sustained P1/P2.

## Explicit residuals and non-claims

- Phase 4 has no new published workflow run and is not described as “CI green”.
  The Phase 3 published baseline remains Admin `2 failed / 2 passed` before these
  local Graph changes.
- Simulator visual E2E `6 failed / 13 passed` and its bootstrap
  `ModuleNotFoundError` for `pydantic` were not touched.
- Replay ordering and REN-012B2/REN-012C remain Phase 5 and were not started.
- Invalid legacy Equivalent codes still require manual cleanup.
- Naive `AdminParticipantListItem.created_at` and
  `AdminIncidentItem.created_at` are sibling residuals outside the one confirmed
  Phase 4 audit timestamp path; no speculative schema sweep was made.
  - **Status correction 2026-08-11 (ledger staleness, finding B-4).** This line is
    stale for `AdminIncidentItem.created_at`: the UTC validator landed in `08ba40e`
    and is present at `app/schemas/admin.py:139-147`. The archived body is left
    otherwise unchanged per `docs/ru/documentation-rules.md` §2.4; this is a status
    mark, not a rewrite. The sibling residual that is still real is
    `app/schemas/trustline.py:22-23`, registered in `specs/BACKLOG.md`.
- No visual redesign, complex ARIA canvas model, all-browser matrix or broad
  Cytoscape decomposition is claimed.
