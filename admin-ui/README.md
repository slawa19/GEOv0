# Admin UI (Vue 3 + TypeScript + Vite)

Recommended way to run the Admin UI in this repo (Windows): use the repo runner script.

```powershell
.\scripts\run_local.ps1 start
```

It starts the backend and Admin UI, manages ports/PIDs under `.local-run/`, and writes `admin-ui/.env.local` with the backend URL.

## API modes

Admin UI supports two modes:

- **Mock mode** (default) — UI reads deterministic JSON fixtures from `admin-ui/public/admin-fixtures/...` via the mock API layer.
- **Real mode** — UI calls the backend HTTP API.

The repo runner (`.\scripts\run_local.ps1 start`) configures **real mode** by writing `admin-ui/.env.local`.

If you run the UI without the runner:

- Mock mode:
	- `VITE_API_MODE=mock` (or leave unset)
- Real mode:
	- `VITE_API_MODE=real`
	- `VITE_API_BASE_URL=http://127.0.0.1:18000` (runner default)
	- or `VITE_API_BASE_URL=http://127.0.0.1:8000` (Docker Compose default)

Canonical docs (RU): `docs/ru/admin-ui/README.md`.

Technical note (EN) on real API integration: `admin-ui/docs/real-api-integration.md` (auth token, endpoint mapping, proxy notes).

## Fixtures

In **mock mode**, the dev server reads JSON fixtures from `admin-ui/public/admin-fixtures/...`.

- `npm run sync:fixtures` copies canonical fixtures from `../admin-fixtures` into `public/`.
- `npm run validate:fixtures` checks that canonical+public fixtures are parseable, `_meta.json` matches, `seed_id` is allow-listed, and participant/trustline fields follow the deterministic constraints (including supported participant types `person|business|hub`).

`npm run dev` runs `sync:fixtures` + `validate:fixtures` automatically via `predev`.
