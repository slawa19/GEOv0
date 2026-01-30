# GEO Simulator UI

This folder contains two versions:

- `v2/` — **current** demo-fast-mock v2 (active development)
- `v1/` — legacy prototype (archived, outdated)

## Run v2 (Windows)

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

## Real mode note (important)

When running the UI in **Real Mode** against the backend:

- After any backend code change (especially SSE/event payload changes), **restart the backend**.
	Otherwise the UI can keep working but will receive old event shapes (e.g. missing `node_patch`), which makes balances/colors/sizes look "stuck".
