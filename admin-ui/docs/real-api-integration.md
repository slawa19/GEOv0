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
### Option A: use a dev proxy (recommended)
1) Set the proxy target:
- `VITE_API_PROXY_TARGET=http://localhost:8000`

2) Run dev server:
- `npm run dev`

The UI will call relative paths like `/api/v1/...`, and Vite will proxy them to the backend.

### Option B: direct base URL (no proxy)
- Set `VITE_API_BASE_URL=http://localhost:8000`

## 3) Auth / headers
OpenAPI indicates admin endpoints use an `X-Admin-Token` header (and often disable BearerAuth for admin routes).

Recommended approach for UI:
- store admin token in localStorage (dev-only)
- attach it on every `/api/v1/admin/*` request

Keys:
- `admin-ui.adminToken` (string)

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

Equivalents (admin):
- `GET /api/v1/admin/equivalents`
- `POST /api/v1/admin/equivalents`
- `PATCH /api/v1/admin/equivalents/{code}`
- `DELETE /api/v1/admin/equivalents/{code}`
- `GET /api/v1/admin/equivalents/{code}/usage`

Integrity:
- `GET /api/v1/integrity/status`
- `POST /api/v1/integrity/verify`

Not yet available in backend (fixtures-only today):
- incidents list and abort-tx. In `VITE_API_MODE=real` these calls currently raise a `NOT_IMPLEMENTED` error.

## 5) Error/envelope expectations
UI expects an `ApiEnvelope<T>` shape:
- success: `{ success: true, data: ... }`
- error: `{ success: false, error: { code, message, details? } }`

If the backend returns a different shape, adapt inside `realApi` (do not fix every page).

Known shape differences already adapted in `realApi`:
- `GET /api/v1/admin/config` returns `{items:[{key,value,...}]}` → UI is expecting a flat `{[key]:value}`.
- Admin list endpoints don’t provide `total/page/per_page` consistently → UI currently derives `total` heuristically.
- Participants status vocabulary differs (`suspended/deleted` vs `frozen/banned`) → mapped in adapter.

## 6) Development checklist
- Start backend at `http://localhost:8000` (or adjust proxy target)
- Ensure admin token is configured (if required by endpoint)
- Switch `VITE_API_MODE=real`
- Verify pages:
  - Dashboard loads health + migrations
  - Config/Feature Flags load and save
  - Participants/Trustlines paginate
  - Audit log loads

