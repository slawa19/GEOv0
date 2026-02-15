# GEO Simulator UI — Demo Fast Mock v2 (WIP)

This folder is the *current* home for the demo-fast-mock v2 implementation.

Legacy / previous prototype was archived to `simulator-ui/v1/` and should be treated as outdated reference.

## Run (Windows)

From repo root:

- PowerShell: `./scripts/run_simulator_ui.ps1`
- cmd.exe: `scripts\\run_simulator_ui.cmd`

Tip (real mode / proxy changes): if the UI is already running and you need it to pick up new env/proxy settings, use:

- PowerShell: `./scripts/run_simulator_ui.ps1 -RestartIfRunning`

Or manually:

```bash
cd simulator-ui/v2
npm install
npm run dev
```

Open: http://localhost:5176/

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

2) Click scenarios refresh (↻), select a scenario
3) Click `Start run`
4) Confirm:
	- `run_status` heartbeat updates (state/sim_time/ops/queue)
	- graph snapshot renders
	- `tx.updated` / clearing events animate when present

---

## Interact Mode (Real Mode UI)

Interact Mode is a **Real Mode** UI variant that enables a small set of *manual actions* against a running simulator run.

### 1) Enable backend action endpoints

Backend action endpoints are feature-flagged.

- Set `SIMULATOR_ACTIONS_ENABLE=1` **before starting the backend**.
- When the flag is off, calling any `/api/v1/simulator/runs/{run_id}/actions/*` endpoint returns HTTP **403** with JSON error code `ACTIONS_DISABLED`.

### 2) Run UI and open Interact Mode

Start the UI in real mode (see section above) and open:

- `http://localhost:5176/?mode=real&ui=interact`

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

