# Admin UI (Vue 3 + TypeScript + Vite)

Recommended way to run the Admin UI in this repo (Windows): use the repo runner script.

```powershell
.\scripts\run_local.ps1 start
```

It starts the backend and Admin UI, manages ports/PIDs under `.local-run/`, and writes `admin-ui/.env.local` with the backend URL.

## Backend

The Admin UI always calls the backend HTTP API. It has no mock mode, no fixtures and no role selector (removed 2026-10-07, programme 032 slice S4); the backend must be running.

The repo runner (`.\scripts\run_local.ps1 start`) starts it and writes `admin-ui/.env.local`. If you run the UI without the runner:

- `VITE_API_BASE_URL=http://127.0.0.1:18000` (runner default; in dev the default is the same) or `http://127.0.0.1:8000` (Docker Compose default);
- `VITE_ADMIN_TOKEN` — the backend's `ADMIN_TOKEN`; alternatively localStorage `admin-ui.adminToken`. A dev server without a token uses the backend's default dev token; a production build without a token refuses admin requests with 401 `ADMIN_TOKEN_MISSING`;
- seed the database with `python scripts/seed_db.py --source recipe --community riverside-town-50` (the runner does it itself).

RBAC is not implemented: the UI has no roles; the backend enforces the admin token.

Canonical docs (RU): `docs/ru/admin-ui/README.md`.

Technical note (EN) on real API integration: `admin-ui/docs/real-api-integration.md` (auth token, endpoint mapping, proxy notes).

## E2E

Every Admin e2e runs against a real seeded backend: `.\scripts\verify_admin_e2e.ps1 -TaskSlug <slug>` (add `-Smoke` for the blocking smoke only) brings up a disposable database, seeds it, starts the backend and runs Playwright (`ADMIN_E2E_BACKEND_ORIGIN` and `ADMIN_E2E_TOKEN` are required). Degraded states are produced by `page.route` in the tests. Manual check: `docs/ru/admin-ui/manual-smoke-real-mode.md`.
