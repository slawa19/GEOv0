# GEO Simulator UI — Demo Fast Mock v2 (WIP)

This folder is the *current* home for the demo-fast-mock v2 implementation.

Legacy / previous prototype was archived to `simulator-ui/v1/` and should be treated as outdated reference.

## Run (Windows)

From repo root:

- PowerShell: `./scripts/run_simulator_ui.ps1`
- cmd.exe: `scripts\\run_simulator_ui.cmd`

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
- Backend running locally (default): `http://127.0.0.1:8000`
- A valid **access token** (Bearer) for GEO Hub API

### Run (PowerShell)

From `simulator-ui/v2`:

```powershell
$env:VITE_API_MODE='real'
$env:VITE_GEO_API_BASE='/api/v1'

# Optional: proxy target if your backend is not on 8000
# $env:VITE_GEO_BACKEND_ORIGIN='http://127.0.0.1:8000'

npm run dev
```

Notes:
- The Vite dev server proxies `/api/v1/*` to the backend (including SSE), so the browser stays same-origin.
- The token can be pasted into the HUD and is persisted in localStorage (dev convenience).

### Quick manual smoke
1) Set `mode=real` (env above does it) and open http://localhost:5176/
2) Paste token
3) Click scenarios refresh (↻), select a scenario
4) Click `Start run`
5) Confirm:
	- `run_status` heartbeat updates (state/sim_time/ops/queue)
	- graph snapshot renders
	- `tx.updated` / clearing events animate when present

## E2E screenshots (Playwright)

From `simulator-ui/v2`:

- Run screenshot assertions: `npm run test:e2e`
- Update snapshots (only for intentional visual changes): `npm run test:e2e:update`


