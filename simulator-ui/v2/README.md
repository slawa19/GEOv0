# GEO Simulator UI — Demo Fast Mock v2 (WIP)

This folder is the *current* home for the demo-fast-mock v2 implementation.

The previous prototype `simulator-ui/v1/` was removed 2026-10-16 (041 `T4106`); it is kept in git under the tag `legacy/simulator-ui-v1-removed`.

## Run (Windows)

From repo root:

- PowerShell: `./scripts/run_simulator_ui.ps1`
- cmd.exe: `scripts\\run_simulator_ui.cmd`

`scripts/run_simulator_ui.ps1` opens the **offline snapshot** by default (`-Mode fixtures`: it sets `VITE_API_MODE=fixtures` and prints `?mode=fixtures`); `-Mode real` opens the server source and needs a reachable backend. A bare `npm run dev` opens the server source (see "The demo screen").

Tip (real mode / proxy changes): if the UI is already running and you need it to pick up new env/proxy settings, use:

- PowerShell: `./scripts/run_simulator_ui.ps1 -RestartIfRunning`

Or manually:

```bash
cd simulator-ui/v2
npm install
npm run dev
```

Open: http://localhost:5176/

## The demo screen (TopBar + BottomBar)

Since 043 (S1-S3, 2026-10-16/17) the UI is **one screen**: there is no Sandbox / Auto-Run / Interact switch and no `?ui=` mode. `?ui=interact` and `?ui=demo` in an old link are ignored - the same screen opens (`useSimulatorApp.ts`, `isLiveUi`).

**Opening.** The source is the **server** by default (`apiMode` falls back to `real` when neither `?mode=` nor `VITE_API_MODE` says otherwise, `useSimulatorApp.ts` `apiMode`). When the opening is over the screen makes **one** attempt per page load to start a live run with the existing `startRun` (the stored intensity, 30 % when none; not paused). A live run found on the server - paused included - is left as it is; a run the person stops afterwards is not restarted (`useInteractAutoBootstrapRun.ts`, `autostartDecision`). If the server refuses because its limit of active runs is reached (409 `conflict_kind: global_active_limit`), the error line is a readable text, not an HTTP string: "Демо уже кто-то смотрит на этом сервере; попробуйте позже. Запустить снова - Start в «Подробнее»." (`utils/runErrorClassification.ts`, `startRunErrorText`). The page language is Russian (`index.html` `<html lang="ru">`); the texts that follow the interface language (`uiLocale()`) are Russian accordingly, the English strings of `i18n/interactStrings.ts` are unchanged.

**First view** (the "Подробнее" toggle off, the default): the first row of the TopBar - network name and state, the "Поток" button, the three actions (Send Payment, Manage Trustline, Run Clearing) and "Подробнее"; the BottomBar - the legend, the total debt and "Reset view"; one onboarding hint (once dismissed it does not come back, `geo.sim.v2.onboarding.dismissed`). Everything else is **not rendered** until "Подробнее" is on.

**"Подробнее"** (one flag, remembered in the browser, `geo.sim.v2.details.open`) opens the service rows:

- TopBar: Theme, scenario and run controls (Start / Pause / Resume / Stop, Intensity + Apply; server source only), the SSE / Run / TX badges, Admin, the full system metrics;
- BottomBar: EQ, Layout, **"Источник"**, Scene (offline snapshot only), Quality, Labels, Refresh, Analytics and Artifacts (server source only) and "Inspect graph".

**Pause on a manual act.** Any explicit manual act pauses the run (`POST .../pause`): starting an action (Send Payment, Manage Trustline, Run Clearing, a node card, a line's popup), choosing a participant or a line, choosing a sender, recipient, creditor or debtor (`useSimulatorApp.ts`, the entries wrapped around `interactMode`, `noteManualAction`). Confirm, cancel, Escape and a closed panel do **not** resume. The flow comes back only by an explicit act: the "Поток" button (⏸ pauses a living run, ▶ resumes a paused one) or Resume under "Подробнее". A selection made by loading (a `?focus=` deep link) is not an act.

**"Источник"** (under "Подробнее") switches between "Сервер" (a live run) and "Снимок без сервера" (the offline snapshot on static fixtures; no run, no server requests). It replaces the whole data pipeline, so it reloads the page with `?mode=` - the one switch of this screen that still navigates (`SimulatorAppRoot.vue`, `setSource`). Entering a manual action never navigates. The offline snapshot is also reached with `?mode=fixtures` or `VITE_API_MODE=fixtures`.

Notes:
- The backend run mode `fixtures` was removed on 2026-10-16 (programme 042): a run is always `real`; "offline" is a client-only choice.
- The Dev Tools shell, Demo UI (`?ui=demo`), FX Debug buttons, the `VITE_ALLOW_DEMO_UI` gate, the `geo.sim.v2.devtools.open.*` and `fxDebugRun` keys and the `?mode=demo` alias were removed in 043 S3 (2026-10-17). `?debug=1` is accepted in a URL but nothing reads it any more; the source switch drops `?ui=` and `?debug=`. The "Single Tx" / "Run Clearing" debug buttons, `window.__geoSim.runTxOnce` / `runClearingOnce` and the backend actions `tx-once` / `clearing-once` were removed on 2026-10-16 (programme 042): a payment or clearing event is born in the core only.

### Test and automation switches (glossary)

These are not end-user settings - they keep automation stable.

- **DEV** (`import.meta.env.DEV`): running via Vite dev server (`npm run dev`); dev-only warnings and the `window.__geoSim` hook.
- **WebDriver** (`navigator.webdriver=true`): the browser is controlled by automation (Playwright/Selenium).
- **Test mode** (`VITE_TEST_MODE=1`): enables deterministic behavior for visual/screenshot tests (e.g., FX spawning is suppressed) and shows the `TEST MODE` badge under "Подробнее".
- **E2E screenshots**: effectively "test mode + WebDriver". In this case the app forces the fixtures/offline pipeline for deterministic snapshots.

Dev hook:

- In DEV builds only, the UI installs `window.__geoSim` (see `src/dev/geoSimDevHook.ts`) to help debugging and E2E harnesses. It is not available in production builds.

## Scenes (A–E)

The **Scene** selector (A–E) switches the demo into predefined, repeatable states.
This is used both for manual QA and Playwright screenshot tests.

- **A — Overview**: baseline overall graph view
- **B — Focus**: selection/focus behavior
- **C — Statuses**: status/color mapping coverage
- **D — Tx burst**: single deterministic transaction animation
- **E — Clearing**: deterministic clearing step animation

Tip: you can deep-link for deterministic repro:

- `/?scene=A`
- `/?scene=D&layout=admin-force`

Supported `layout` values: `admin-force`, `community-clusters`, `balance-split`, `type-split`, `status-split`.

## Demo fixtures vs test-mode

There are two relevant modes controlled by Vite env vars:

- `VITE_DEMO_FIXTURES` (default: **1**) — loads local fixtures and locks EQ to UAH.
- `VITE_TEST_MODE` (default: **0**) — enables deterministic behavior for screenshot tests.
	Some interactive UI is hidden in test-mode to avoid non-deterministic diffs.

The demo snapshots behind `VITE_DEMO_FIXTURES` (`public/simulator-fixtures/v1/<EQ>/snapshot.json`, `events/demo-tx.json`, `events/demo-clearing.json` and their `events/*-snapshot.json`) are static, versioned assets. Their generator, `scripts/sync_demo_fixtures.ps1` and the `predev` / `prebuild` hooks (`npm run sync:demo-fixtures`) were deleted on 2026-10-07 (programme 032, slice S4): edit the snapshots directly when a demo needs to change; the playlists are re-recorded, not edited (039 `T3935`). The Simulator UI's own fixtures mode stays.

**Offline effects workbench (042 `T4205`, 2026-10-18).** On the offline source («Подробнее» → «Источник» → «Снимок без сервера», or `?mode=fixtures`) the bottom service row offers «Плейлист» with «Проиграть» / «Стоп» / «Шаг»: the recorded playlists of the snapshot's equivalent (`events/*.json`) are played through the same event path as the server's SSE events (`normalizeSimulatorEvent` → `applyAcceptedRealEvent` → `executeRealEventIntents`, `src/composables/usePlaylistWorkbench.ts`), so beams, bursts and clearing pulses can be watched and tuned without a server. A playlist must be in the SSE event form: only `tx.updated`, `tx.failed` and `clearing.done`, all of the folder's equivalent; a playlist with any other event is not offered (a warning in the console names the event), an empty one is not offered either. Since 2026-10-18 (039 `T3935`) the UAH playlists are recorded from a live run (`scripts/record_workbench_playlists.py`) and each carries its own start snapshot (`events/<id>-snapshot.json`): Play (or a Step that begins a playback) shows that snapshot, waits for its layout to settle and plays; Stop, another playlist, a replaced scene or the end of the playback returns to `<EQ>/snapshot.json`. A playlist without its start snapshot is not offered. EUR and HOUR have no recording (no shipped scenario carries them). How a recording is made and re-made: `public/simulator-fixtures/v1/_meta/README.txt`.

### Enable test-mode manually (PowerShell)

From `simulator-ui/v2`:

```powershell
$env:VITE_TEST_MODE='1'
$env:VITE_DEMO_FIXTURES='1'
npm run dev
```

When `VITE_TEST_MODE=1` and you are not running under Playwright, the HUD shows a small `TEST MODE` pill.

---

## Real Mode (backend integration)

The UI can run in **Real Mode** (REST control-plane + SSE data-plane) against the backend simulator API.

### Prereqs
- Backend running locally (default): `http://127.0.0.1:18000` (see `scripts/run_local.ps1 -BackendPort`)
- Auth:
	- **Admin token** (recommended for dev/admin flows): send it as `X-Admin-Token`.
		- In **dev on localhost**, the UI auto-uses the dev admin token (default: `dev-admin-token-change-me`) and shows `dev admin (auto)` in the HUD.
	- **Bearer token** (JWT): obtained via `POST /api/v1/auth/login` and sent as `Authorization: Bearer <token>`.

### After backend changes (required)

If you change backend code while using Real Mode (especially anything related to SSE events like `tx.updated`, `node_patch`, `edge_patch`, `viz_*`):

1) **Restart the backend**.
2) Start a **new run** in the UI.

Without a backend restart, the UI may keep running but will receive old event payloads, which looks like "transactions animate but net/color/size never change".

### Run (PowerShell)

From `simulator-ui/v2`:

```powershell
$env:VITE_API_MODE='real'
$env:VITE_GEO_API_BASE='/api/v1'

# Optional: proxy target if your backend is not on 18000
# $env:VITE_GEO_BACKEND_ORIGIN='http://127.0.0.1:18000'

npm run dev
```

Notes:
- The Vite dev server proxies `/api/v1/*` to the backend (including SSE), so the browser stays same-origin.
- Token handling:
	- The token is persisted in `localStorage`.
	- Dev auto-token can be overridden via `VITE_GEO_DEV_ACCESS_TOKEN`.
	- To force manual entry, set `VITE_GEO_DEV_ACCESS_TOKEN=''` and clear `localStorage` key `geo.sim.v2.accessToken`.

### Quick manual smoke
1) Set `mode=real` (env above does it) and open http://localhost:5176/

2) The screen starts a live run by itself once the opening is over (one attempt per page load). To pick another scenario or to start again, open "Подробнее", refresh scenarios (↻), select one and click `Start`
3) Confirm:
	- `run_status` heartbeat updates (state/sim_time/ops/queue)
	- graph snapshot renders
	- `tx.updated` / clearing events animate when present

---

## Manual actions (server source)

The manual actions (payment, trustline, clearing) are part of the one demo screen on the **server source**; there is no separate Interact mode or `?ui=interact` URL since 043 S2. They act on the running simulator run and pause its automatic payments (see "The demo screen").

### 1) Enable backend action endpoints

Backend action endpoints are feature-flagged.

- Set `SIMULATOR_ACTIONS_ENABLE=1` **before starting the backend**.
- It gates the Interact actions (`payment-real`, `clearing-real`, `trustline-*`, lists); the debug actions `tx-once` / `clearing-once` no longer exist (042).
- When the flag is off, calling any `/api/v1/simulator/runs/{run_id}/actions/*` endpoint returns HTTP **403** with JSON error code `ACTIONS_DISABLED`.

### 2) Run UI and open the demo

Start the UI in real mode (see section above) and open:

- `http://localhost:5176/` (or `?mode=real`) - the three actions are in the first row.

### 3) Example scenario fixture

Example scenario JSON (fixtures):

- `fixtures/simulator/clearing-demo-10/scenario.json`

### Troubleshooting: "balances / colors / sizes don't change"

- Verify SSE payload contains patches:
	- In DevTools → Network → the `/events` request → a `tx.updated` item should include `node_patch` and `edge_patch`.
- Verify you're connected to the expected backend:
	- The UI persists API base and token in `localStorage` (keys like `geo.sim.v2.apiBase`).
	- If you started a backend on a different port/origin, update the API base in the UI and reload.

## E2E screenshots (Playwright)

From `simulator-ui/v2`:

- Run screenshot assertions: `npm run test:e2e`
- Update snapshots (only for intentional visual changes): `npm run test:e2e:update`

