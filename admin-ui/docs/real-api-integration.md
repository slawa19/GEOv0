# Admin UI → Real API integration

This Admin UI currently runs against deterministic JSON fixtures via a mock API layer.

Goal: make switching to real GEO Hub endpoints low-friction, without rewriting pages.

## 1) Current architecture
- Pages should import `api` from `src/api/index.ts` (single entrypoint).
- `api` can be backed by:
  - `mockApi` (fixtures) for UI prototyping
  - `realApi` (fetch) for real backend

As of 2026-01-12, pages/stores no longer import `mockApi` directly.

## 2) Configure API mode
### Option B: direct base URL (recommended in this repo)
Set a direct API base URL:

- `VITE_API_BASE_URL=http://localhost:8000`

Why this is the recommended path here:
- Dev proxy is **not configured** in `admin-ui/vite.config.ts` right now.
- With no proxy, browser requests to `/api/v1/...` would go to the Vite origin (`localhost:5173`) and fail.

### Option A: use a dev proxy (only if you add Vite proxy config)
If you prefer proxy-based dev (to avoid CORS), you must add `server.proxy` in `admin-ui/vite.config.ts`.

After that, you can set:
- `VITE_API_PROXY_TARGET=http://localhost:8000`

and run:
- `npm run dev`

## 3) Auth / headers
OpenAPI indicates admin endpoints use an `X-Admin-Token` header (and often disable BearerAuth for admin routes).

Recommended approach for UI:
- store admin token in localStorage (dev-only)
- attach it on every `/api/v1/admin/*` request

Keys:
- `admin-ui.adminToken` (string)

Dev convenience (intentional):
- In `real` mode, when running the UI in dev (`import.meta.env.DEV`), if no token is set yet the UI auto-seeds localStorage with the backend default token: `dev-admin-token-change-me`.
- This is a dev-only ergonomics hack to avoid first-run 403 spam and make "just open the UI" testing frictionless.
- Override it with `VITE_ADMIN_TOKEN=...` (recommended for teams) or by setting `localStorage['admin-ui.adminToken']`.

## 4) Endpoint mapping
The `realApi` skeleton is expected to implement the same surface as `mockApi`, but using real endpoints.

Use [api/openapi.yaml](../api/openapi.yaml) as the contract source of truth.

Common endpoints used by pages:
- `GET /api/v1/health`
- `GET /api/v1/health/db`
- `GET /api/v1/admin/migrations`
- `GET /api/v1/admin/config` (+ `X-Admin-Token`)
- `PATCH /api/v1/admin/config` (+ `X-Admin-Token`)
- `GET /api/v1/admin/feature-flags` (+ `X-Admin-Token`)
- `PATCH /api/v1/admin/feature-flags` (+ `X-Admin-Token`)
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

Previously fixtures-only, now available in backend:
- Incidents list and abort-tx are implemented as admin endpoints; `realApi` calls them directly.
- Graph snapshot/ego and admin clearing cycles are implemented; `realApi` calls them directly.

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
- Start backend at `http://localhost:8000` (or adjust proxy target)
- Ensure admin token is configured (if required by endpoint)
- Switch `VITE_API_MODE=real`
- Verify pages:
  - Dashboard loads health + migrations
  - Config/Feature Flags load and save
  - Participants/Trustlines paginate
  - Audit log loads

## 7) End-user testing (Windows quickstart)

If you see `ERR_CONNECTION_REFUSED` on `http://localhost:5173/`, use the repo runner script below (run from repo root) — it starts both backend and UI with deterministic ports and avoids PowerShell quoting issues.

```powershell
.\scripts\run_local.ps1 start
```

### 7.1 Start backend + DB (Docker Compose)
From repo root:

- `docker compose up -d --build`

Migrations run automatically on container start (see `docker/docker-entrypoint.sh`).

Optional seed:
- `docker compose exec app python scripts/seed_db.py`

### 7.1b Start backend locally (no Docker)

If Docker is unavailable, you can run the backend locally on SQLite:

- Initialize DB schema (creates `geov0.db` in repo root):
  - `python scripts/init_sqlite_db.py`
- Seed demo data:
  - Recommended for Admin UI testing (fixtures-like rich dataset): `python scripts/seed_db.py --source fixtures`
  - Choose a full community pack without modifying tracked fixtures (writes to `.local-run/fixture-packs`):
    - `python scripts/seed_db.py --source fixtures --community greenfield-village-100`
    - `python scripts/seed_db.py --source fixtures --community riverside-town-50`
  - Legacy small seed set: `python scripts/seed_db.py --source seeds`
- Run API:
  - `python -m uvicorn app.main:app --reload --port 8000`
  - If `8000` is unavailable on Windows, use `--port 18000`.

### 7.2 Configure Admin UI for real-mode
Create `admin-ui/.env.local` (or copy from `admin-ui/.env.local.example`) with:

- `VITE_API_MODE=real`
- `VITE_API_BASE_URL=http://localhost:8000`

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

If your backend uses a different token, set one of:
- `VITE_ADMIN_TOKEN=...` in `admin-ui/.env.local` (preferred)
- `localStorage.setItem('admin-ui.adminToken', '<token>')`

### 7.4 URL to open
- `http://localhost:5173/`

