# Admin UI → Real API integration

This Admin UI always calls the real GEO Hub backend. The mock API layer, its fixtures and the mode switch were
deleted on 2026-10-07 (programme 032, slice S4); there is no `VITE_API_MODE` and no mock to fall back on.

## 1) Current architecture
- Pages import `api` from `src/api/index.ts` (single entrypoint); it is `realApi` (fetch) and nothing else
  (`src/api/singleClient.guard.test.ts` keeps it that way).

## 2) Configure the backend address
### Option B: direct base URL (recommended in this repo)
Set a direct API base URL:

- If you start the backend via `scripts/run_local.ps1`, the default is:
  - `VITE_API_BASE_URL=http://127.0.0.1:18000`
- If you start the backend via Docker Compose (default port mapping), use:
  - `VITE_API_BASE_URL=http://127.0.0.1:8000`

Why this is the recommended path here:
- Dev proxy is **not configured** in `admin-ui/vite.config.ts` right now.
- With no proxy, browser requests to `/api/v1/...` would go to the Vite origin (`localhost:5173`) and fail.

### Option A: use a dev proxy (only if you add Vite proxy config)
If you prefer proxy-based dev (to avoid CORS), you must add `server.proxy` in `admin-ui/vite.config.ts`.

After that, you can set:
- `VITE_API_PROXY_TARGET=http://127.0.0.1:18000` (runner default)
- or `VITE_API_PROXY_TARGET=http://127.0.0.1:8000` (Docker default)

and run:
- `npm run dev`

## 3) Auth / headers
OpenAPI indicates admin endpoints use an `X-Admin-Token` header (and often disable BearerAuth for admin routes).

Recommended approach for UI:
- store admin token in localStorage (dev-only)
- attach it on every `/api/v1/admin/*` request

Keys:
- `admin-ui.adminToken` (string)

Roles (not auth):
- The UI has no role selector and no read-only mode: the `admin/operator/auditor` selector (`admin-ui.role`) was mock-only and was removed on 2026-10-07 (032 S4).
- RBAC is not implemented (recorded decision of programme 022); the backend enforces the admin token.

Without a token:
- A production build refuses admin requests with 401 `ADMIN_TOKEN_MISSING`.

Dev convenience (intentional):
- When running the UI in dev (`import.meta.env.DEV`), if no token is set yet the UI uses the backend default token: `dev-admin-token-change-me`.
- This is a dev-only ergonomics hack to avoid first-run 403 spam and make "just open the UI" testing frictionless.
- Override it with `VITE_ADMIN_TOKEN=...` (recommended for teams) or by setting `localStorage['admin-ui.adminToken']`.

## 4) Endpoint mapping
Use [api/openapi.yaml](../../api/openapi.yaml) as the contract source of truth.

Common endpoints used by pages:
- `GET /api/v1/health`
- `GET /api/v1/health/db`
- `GET /api/v1/admin/migrations`
- `GET /api/v1/admin/config` (+ `X-Admin-Token`)
- `PATCH /api/v1/admin/config` (+ `X-Admin-Token`)
- `GET /api/v1/admin/participants`
- `GET /api/v1/admin/trustlines`
- `GET /api/v1/admin/audit-log`
- `GET /api/v1/admin/incidents`
- `POST /api/v1/admin/transactions/{tx_id}/abort`
- `GET /api/v1/admin/graph/snapshot`
- `GET /api/v1/admin/graph/ego?pid=...&depth=1|2`
- `GET /api/v1/admin/clearing/cycles` (optional: `participant_pid`, `equivalent`, `max_depth`)

Equivalents (admin):
- `GET /api/v1/admin/equivalents`
- `POST /api/v1/admin/equivalents`
- `PATCH /api/v1/admin/equivalents/{code}`
- `DELETE /api/v1/admin/equivalents/{code}`
- `GET /api/v1/admin/equivalents/{code}/usage`

Integrity:
- `GET /api/v1/integrity/status`
- `POST /api/v1/integrity/verify`

Feature flags are config keys: the Config page edits them through `PATCH /api/v1/admin/config`. The UI no longer
uses `/admin/feature-flags` (the `FeatureFlagsPage` and its client methods were deleted on 2026-10-07, 032 S4;
`/feature-flags` in the UI redirects to `/config`).

## 5) Error/envelope expectations
UI expects an `ApiEnvelope<T>` shape:
- success: `{ success: true, data: ... }`
- error: `{ success: false, error: { code, message, details? } }`

If the backend returns a different shape, adapt inside `realApi` (do not fix every page).

Known shape differences already adapted in `realApi`:
- `GET /api/v1/admin/config` returns `{items:[{key,value,...}]}` → UI is expecting a flat `{[key]:value}`.
- Admin list endpoints now return `{ items, page, per_page, total }` (backend-aligned); UI consumes these when present.
- Participants status vocabulary differs (`suspended/deleted` vs `frozen/banned`) → mapped in adapter.

Audit log note:
- `GET /api/v1/admin/audit-log` supports server-side `q` search (needle) so the UI search box is not a misleading single-page filter.

## 6) Development checklist
- Start backend at `http://127.0.0.1:18000` (runner default) or `http://127.0.0.1:8000` (Docker default)
- Ensure admin token is configured (if required by endpoint)
- Verify pages:
  - Dashboard loads health + migrations
  - Config (including the feature-flag keys) loads and saves
  - Participants/Trustlines paginate
  - Audit log loads

## 7) End-user testing (Windows quickstart)

If you see `ERR_CONNECTION_REFUSED` on `http://localhost:5173/`, use the repo runner script below (run from repo root) — it starts both backend and UI with deterministic ports and avoids PowerShell quoting issues.

```powershell
.\scripts\run_local.ps1 start
```

Note: `run_local.ps1 start` writes/updates `admin-ui/.env.local` with:
- `VITE_API_BASE_URL=http://127.0.0.1:<backendPort>` (default `18000`)

Pick a community (its recipe is run through the domain services; `riverside-town-50` is the default):

```powershell
.\scripts\run_local.ps1 reset-db -SeedCommunity greenfield-village-100
.\scripts\run_local.ps1 start -SeedCommunity greenfield-village-100
```

Seeding by importing fixture packs into the database (`seed_db.py --source fixtures` / `--source seeds`) was
removed by programme 030 S2 (`F-030-3`): demo data goes through the real API with the same checks. The
`admin-fixtures/` datasets, which were the mock-mode data of the Admin UI, were deleted together with the mock
mode on 2026-10-07 (032 S4).

### 7.1 Start backend + DB (Docker Compose)
From repo root:

- Local development, with the repository's explicit dev defaults:
  `docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build`
- Production-like startup uses only `docker-compose.yml`, but first requires
  non-placeholder `JWT_SECRET`, `ADMIN_TOKEN`, `SIMULATOR_SESSION_SECRET`, and a
  valid `SIMULATOR_CSRF_ORIGIN_ALLOWLIST`. Running the base file without those
  values is expected to fail fast.

Migrations run automatically on container start (see `docker/docker-entrypoint.sh`).

Seeding: the Compose database `geov0` is not seeded. The recipe (`scripts/seed_db.py --source recipe`) seeds only
an empty, disposable database named by contract (`geov0_dev_<slug>` / `geov0_test_<slug>` on a loopback host) and
refuses any other; for a seeded stack use `run_local.ps1` (above) or 7.1b.

### 7.1b Start backend locally (no Docker)

If Docker is unavailable, run the backend against a local PostgreSQL
([`docs/ru/backend/postgres-local-portable.md`](../../docs/ru/backend/postgres-local-portable.md)); SQLite was
removed in programme 017 and `DATABASE_URL` must be `postgresql+asyncpg://...`.
`.\scripts\run_local.ps1 start` creates, migrates and seeds its own database `geov0_dev_<DbSlug>`.
By hand, with `DATABASE_URL` set:

- Initialize DB schema:
  - `python -m alembic -c migrations/alembic.ini upgrade head`
- Seed demo data (into an empty `geov0_dev_<slug>` database) by running a community's recipe:
  - `python scripts/seed_db.py --source recipe --community riverside-town-50`
  - `python scripts/seed_db.py --source recipe --community greenfield-village-100`
  - then `python scripts/dev_database.py adopt --community <the same community>`

Note on Windows terminals:
- Python code snippets (e.g. DB checks) must be run with `python` / `.venv\Scripts\python.exe`.
- If you paste Python code into PowerShell, you'll get PowerShell `ParserError` and `The term 'db' is not recognized...` errors.

Quick DB sanity check:
- Via the repo runner: `.\scripts\run_local.ps1 check-db`
- An existing root `geov0.db` or `.local-run/geov0.db` is user data from the SQLite era: nothing
  reads, moves or deletes it.
- Run API:
  - `python -m uvicorn app.main:app --reload --port 18000`
  - If `18000` is unavailable on Windows, use another port and set `VITE_API_BASE_URL` accordingly.

### 7.2 Configure Admin UI
Create `admin-ui/.env.local` (or copy from `admin-ui/.env.local.example`) with:

- `VITE_API_BASE_URL=http://127.0.0.1:18000` (runner default)
  - or `VITE_API_BASE_URL=http://127.0.0.1:8000` (Docker default)

Optional (if backend `ADMIN_TOKEN` is not the default):

- `VITE_ADMIN_TOKEN=...`

Run the UI:
- `npm --prefix admin-ui install`
- `npm --prefix admin-ui run dev`

Note: if you want to force Vite to a fixed port, use:

- `npm --prefix admin-ui run dev -- --port 5173 --strictPort`

### 7.3 Configure admin token in browser
Admin routes require `X-Admin-Token`.

Default token on backend: `dev-admin-token-change-me` (env var `ADMIN_TOKEN`).

You should not need to do anything for local dev:
- In dev, the UI auto-uses the default token (`dev-admin-token-change-me`) if nothing is configured yet.
- A production build with no token configured refuses admin requests with 401 `ADMIN_TOKEN_MISSING`.

If your backend uses a different token, set one of:
- `VITE_ADMIN_TOKEN=...` in `admin-ui/.env.local` (preferred)
- `localStorage.setItem('admin-ui.adminToken', '<token>')`

### 7.4 URL to open
- `http://localhost:5173/`

### 7.5 E2E and manual check
Every Admin e2e runs against a real seeded backend: `scripts/verify_admin_e2e.ps1 -TaskSlug <slug>` (`-Smoke` for the
blocking smoke only). Manual check in a browser: [`docs/ru/admin-ui/manual-smoke-real-mode.md`](../../docs/ru/admin-ui/manual-smoke-real-mode.md).
