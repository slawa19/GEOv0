# Phase 5 Simulator event and UI map

- **Date:** 2026-08-09
- **Execution base:** `b714f1f1f6fb7ef4e2adcaf8088ca07c2bb74563`
- **Branch:** `codex/codebase-renovation-phase5`
- **Scope:** REN-012B2 plus REN-012C only
- **Status:** IN PROGRESS — characterization frozen before product edits

This ledger separates observed behavior from the owner-approved intent and the
bounded target for Phase 5. It does not replace the wire contract, code, tests or
fresh gate results. Phase 6 cleanup, backend adapter work, Simulator v1 and visual
redesign remain outside this slice.

## Pipeline and owner boundary

The supported real-mode path is:

```text
HTTP/SSE input
  -> endpoint decoder or normalizeSimulatorEvent
  -> frame/context/replay acceptance
  -> trusted RealModeState / SimulatorAppState mutation
  -> render wakeup
  -> optional labels / timers / canvas FX
```

`SimulatorAppRoot.vue` is the only product caller of `useSimulatorApp`; the latter
is the only product caller of `useSimulatorRealMode`. Tests call both facades
directly. Phase 5 preserves these facades and the existing REST/SSE shapes.

At the execution base, Phase 3 already rejects malformed/unknown input and wrong
frame/run/equivalent context before cursor, dedup, state and FX
(`normalizeSimulatorEvent.ts:251-564`, `useSimulatorRealMode.ts:528-581`). The
remaining replay decision, state transitions, rendering and FX are still ordered
inside one long `onMessage` callback (`useSimulatorRealMode.ts:583-894`).
The demo activity callback currently wakes rendering before the family-specific
transition (`useSimulatorRealMode.ts:585-593`, `useSimulatorApp.ts:756-762`). A
RAF wake is asynchronous, so this is not by itself a reproduced visual defect,
but the extracted contract must publish the wake intent after state mutation.

## Current / intended / optimal event decisions

| Sequence | Current behavior and evidence | Intended behavior | Optimal bounded target and decision |
|---|---|---|---|
| Start / restart | `startRun` stops the old stream, clears FX/stats, creates or attaches on `409`, clears status/cursor/artifacts, refreshes status/snapshot and starts SSE (`useSimulatorRealMode.ts:961-1040`). A terminal local run is reset before a new start (`967-971`). A REST `restartRun` helper exists but has no product caller; the supported UI restart is terminal → Start. | A new or attached run becomes the single active UI context without old replay/FX leaking into it. | **FIX/CHARACTERIZE:** keep the public action and wire calls; route lifecycle events through one deterministic state transition and explicitly reset replay ownership for the new run. Do not expose a new dedicated restart action. |
| Stop | The client tears down refresh/SSE/FX before awaiting the stop request (`1063-1074`). A `404` resets stale state, but another stop failure leaves a server run potentially active while its UI SSE is already stopped (`1075-1086`). | Stop success or idempotent stale-run recovery is visible; a failed stop must not silently detach the UI from a still-running run. | **FIX:** preserve the current stop request and response contract, but reconnect/retain observation after a non-404 failure. No backend change. |
| Error / lifecycle heartbeat | Accepted `run_status` replaces status, exposes only user-facing `last_error`, and synchronizes authoritative counters (`593-616`). A terminal `stopped|error` status closes the reconnect loop after stream completion (`898-903`). | Lifecycle state and counters are committed before any rendering notification; malformed, stale or duplicate heartbeats do nothing. | **FIX/CHARACTERIZE:** extract a lifecycle state transition returning no visual FX and deterministic render/status intents only when needed. |
| Duplicate event | The callback assigns `lastEventId`, then `markEventProcessed` drops an already-seen ID (`585-590`). Existing tests prove duplicate labels/FX are suppressed (`useSimulatorRealMode.test.ts:657-715`). | Duplicate replay does not mutate trusted state and never schedules render/labels/FX. | **FIX:** make dedup an explicit admission result and advance the cursor only for an accepted, non-duplicate event. |
| Stale run/context | Active-run and equivalent mismatches are rejected before replay/state (`564-581`). HTTP/SSE `404` calls `resetStaleRun`, but snapshot and interact 404 paths duplicate only part of that reset (`useSimulatorApp.ts:697-705,1378-1388`). | Every stale-run decision invalidates the same cursor/dedup/refresh/timer/SSE ownership and falls back to a usable scenario preview. | **FIX:** one narrow stale-run reset owner; preserve preview fallback and existing UI facade. |
| Reconnect / replay | `Last-Event-ID` is sent from `real.lastEventId`; accepted events update it and reconnect reuses it (`sse.ts:68-86`, `useSimulatorRealMode.ts:521-589`). Backend delivery can be non-monotonic across initial status plus prefetched replay (documented Phase 3 residual in `phase3-contract-map.md:39-47`), so a newly observed older accepted event can regress cursor/state. | Reconnect is monotonic for the active run; a late replay frame cannot roll trusted state/cursor backwards or repeat FX. | **FIX:** isolate a monotonic replay cursor decision and reject stale event IDs before state/effects, without changing the wire or adding durable event storage. |
| `410` full refresh | Error text classification clears the cursor, refreshes status then snapshot, marks reconnecting and backs off (`useSimulatorRealMode.ts:907-929`). Existing component evidence covers refresh-before-reconnect (`useSimulatorRealMode.test.ts:1042-1105`). | Replay-too-old recovery deliberately replaces incremental replay with authoritative status/snapshot before reconnect. | **KEEP/EXTRACT:** a narrow typed recovery decision (`full-refresh`) preserving the current ordering and facade. |
| Topology patch | Empty/missing-snapshot events request a full refresh; patch-only and structural payloads mutate the snapshot, apply authoritative patches, update `generated_at`, then wake rendering (`useSimulatorRealMode.ts:728-894`). There is no optional topology FX. | One accepted event either requests authoritative refresh or commits one topology mutation before render; rejected input does neither. | **FIX:** extract deterministic topology application over an owned draft and return only `refresh-snapshot` or `wake-render` intent. Use event `ts`, not wall-clock time. |
| Payment success | Counters and node/edge patches update first, then tx FX, render wakeup, sender label and a guarded delayed receiver label (`619-695`). | Trusted counters/graph state always precede optional FX; a dropped event produces no label, timer or FX. | **FIX:** state application returns small tx effect intents. Execute them afterward; reduced motion may suppress optional canvas FX without suppressing trusted state or status text. |
| Payment failure / cancel | `tx.failed` increments attempt and classifies timeout/internal errors versus domain rejection; it has no FX (`698-713`). Interactive cancellation uses operation epochs/AbortController and ignores a cancelled result (`useInteractMode.ts:165-180,369-389,463-506`); there is no separate SSE cancellation event. | A meaningful rejection/error is announced without decorative FX; user cancellation cannot publish a late success/error. | **KEEP/CHARACTERIZE:** deterministic failed-payment state transition with no FX intent; preserve the existing interact cancellation owner and do not invent a wire event. |
| Clearing completion/patch | Accepted `clearing.done` applies node/edge patches, then runs clearing FX and wakes rendering (`717-725`). Interactive clearing separately performs preview → confirm/running → result and triggers the same visual family after success (`useInteractMode.ts:627-676`). | Authoritative graph/result state precedes optional clearing FX and a user-visible result; rejected/cancelled work emits neither. | **FIX:** return clearing effect intents after state application; keep interactive action ownership and dedup behavior. |

## Current / intended / optimal UI decisions

| Critical path | Current behavior and evidence | Intended behavior | Optimal bounded target and decision |
|---|---|---|---|
| Fixture bootstrap / switch | Fixture mode loads through the existing scene-state facade and the Bottom Bar native scene control; real-mode initial loading is intentionally separate (`useSimulatorApp.ts:1353-1444`, `SimulatorAppRoot.vue:1205-1210`). | Keyboard users can load and switch the existing fixture scenes and observe busy/error/success state. | **CHARACTERIZE:** retain controls/facade and add one browser proof; no fixture/schema change. |
| Real preview / stale recovery | A known active run loads its snapshot; `404` falls back to scenario preview and clears part of the stale context (`useSimulatorApp.ts:1359-1418`). The real-mode boot watcher also probes status before preview (`useSimulatorRealMode.ts:1181-1244`). | A stale persisted run never blocks first preview, and recovery is announced without leaking stale timers/state. | **FIX/CHARACTERIZE:** use the shared stale-reset owner and cover first-load preview in unit/browser evidence. |
| Start / stop / restart / error | Native Top Bar buttons call the preserved `realActions` facade (`SimulatorAppRoot.vue:1036-1053`); status/error content is visible, but not consistently exposed as a live region (`TopBar.vue:331-594`). | All four lifecycle outcomes are keyboard operable and announced. | **FIX:** add bounded status/error/busy announcements around the existing controls; no Top Bar redesign. |
| Payment success / rejection / cancel | The payment panel uses native buttons/input plus an accessible custom select; error and success toasts already use alert/status semantics (`ManualPaymentPanel.vue:369-492`, `SuccessToast.vue:65-74`, `ErrorToast.vue`). Window focus containment exists. | Keyboard entry reaches From/To/amount/confirm/cancel; busy, rejection and success are announced; focus returns to the initiating control when the flow closes. | **FIX/CHARACTERIZE:** preserve the form, add missing focus-restore and busy evidence, and prove one success plus one rejection/cancel. |
| Trustline create / edit / blocked close | The form exposes native inputs/buttons, accessible custom selects and a visible debt-blocked message; focus can enter the limit editor (`TrustlineManagementPanel.vue:67-89,112-185,300-467`). | Create/edit and the blocked-close reason are keyboard reachable and announced; closing restores focus. | **FIX/CHARACTERIZE:** add live semantics/focus evidence only where absent; preserve the two-step destructive confirmation and debt rule. |
| Clearing preview / confirm / result | The panel renders preview/running/result states and uses native confirm/cancel controls (`ClearingPanel.vue:42-123`); success toast exists, but preview/running text is not an explicit live status. | Keyboard entry and focus restoration work, with busy/error/result announcement. | **FIX:** add status semantics and component/browser evidence; preserve the two-phase clearing flow. |
| Node / edge inspect | Product inspection remains canvas-pointer driven (`SimulatorAppRoot.vue:1012-1025,1327-1342`); no DOM navigator exists. Existing NodeCard/EdgeDetail windows are already real DOM. | A practical DOM-based navigator over the current snapshot opens existing node/edge inspectors by keyboard. | **FIX:** one bounded searchable/list navigator using existing collections and window open callbacks; no ARIA canvas model or Cytoscape-like rewrite. |
| Window focus | `WindowShell` provides dialog/group roles, initial focus and Tab containment; the window manager stores/restores opener focus for several close reasons (`WindowShell.vue:42-257`, window-manager tests). Programmatic child cancel/success close paths lack representative restore evidence. | Changed dialogs receive focus on entry and restore it on action, Escape, outside click and successful completion where an opener remains. | **FIX/CHARACTERIZE:** extend the existing window-manager mechanism and component tests; do not add a second focus framework. |
| Canvas / reduced motion | Both canvases are exposed without semantic labeling; the root hard-codes `data-motion="full"` (`SimulatorAppRoot.vue:998-1025`). CSS has isolated reduced-motion rules, but optional tx/clearing canvas FX do not consult the media preference. | The base graph is operable through the DOM alternative; canvas is decorative to assistive technology, and optional FX is disabled for reduced motion. | **FIX:** mark canvases decorative and suppress only optional FX intents under `prefers-reduced-motion`; trusted state/rendering remains active. |

## Frozen implementation boundary

1. Extract narrow admission/replay/recovery functions.
2. Extract accepted lifecycle, topology, payment and clearing state application
   with small post-state effect intents.
3. Keep `useSimulatorRealMode` as transport/orchestration facade and
   `useSimulatorApp` as the product facade.
4. Add the bounded DOM navigator, focus/status semantics and reduced-motion FX
   policy using existing snapshot/window/form owners.
5. Add deterministic unit/component tests and one short non-visual Phase 5
   Chromium spec.

No backend/OpenAPI/wire change is justified by this characterization. No visual
snapshot update is planned.

## Baseline tooling and gates

- Verified executable: `C:\nvm4w\nodejs\node.exe`, Node `v22.12.0`.
- Host npm/npx version is `11.14.0`; the canonical wrappers are
  `C:\nvm4w\nodejs\npm.cmd` and `npx.cmd`.
- Lock/runtime tools: Vitest `3.2.4`, Vite `7.3.1`, Playwright `1.57.0`,
  ESLint `8.57.1`, TypeScript `5.9.3`, Vue TSC package `3.2.2`.
- Canonical package gates remain `lint`, `typecheck`, `test:unit`, `build` and
  scoped `playwright test` under `simulator-ui/v2/package.json`.
- The PowerShell npm shim cannot resolve its install directory inside the current
  filesystem sandbox; direct local binaries resolve. This is environment evidence,
  not a product defect. Canonical npm gates will be run with the required host
  permission rather than replaced by debug-only commands.
- Playwright discovery is available. Actual Chromium execution and the historical
  broader visual baseline remain unverified at this characterization point. The
  installed Playwright `1.57.0` resolves full Chromium to a missing
  `chromium-1200\chrome-win64\chrome.exe`; the available headless-shell cache does
  not yet prove the canonical browser launch.
- The previously logged `ModuleNotFoundError: pydantic` is currently classified as
  an environment/bootstrap observation: non-strict `predev` fixture sync can fall
  back to cached fixtures, the repository `.venv` has Pydantic, and the system
  Python does not. It is not a product defect without a reproduced Phase 5 gate
  failure through the canonical entrypoint.
