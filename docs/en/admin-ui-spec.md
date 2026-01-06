# GEO Hub Admin Console — Detailed UI Specification (Blueprint)

**Version:** 0.2
**Status:** Blueprint for implementation without "guesswork"
**Stack (Recommendation):** Vue.js 3 (Vite), Element Plus, Pinia.

Document aligned with:
- `docs/en/admin-console-minimal-spec.md`
- `docs/en/04-api-reference.md`
- `api/openapi.yaml`

---

## 1. Goals and Scope

### 1.1. Goals (MVP)
- Trust network observability (graph + basic analytics).
- Incident management (stuck transactions → force abort).
- Runtime config and feature flags management.
- Participant management (freeze/unfreeze, ban/unban if available).
- Operator action audit.

### 1.2. Non-Goals (in this version)
- Manual editing of debts/transactions.
- RBAC builder (fixed roles only).

---

## 2. Roles and Access

### 2.1. Roles (Minimal Set)
- `admin` — full access.
- `operator` — operations and config, no critical actions (may be policy-restricted).
- `auditor` — read-only.

### 2.2. Access Errors (Normative)
- `401` → token missing/expired (UI prompts to re-login).
- `403` → insufficient permissions (UI shows read-only or "Insufficient permissions").

---

## 3. Layout and Navigation

### 3.1. UI Principles
- Do not introduce custom "hardcoded" colors/fonts — use design system/theme tokens.
- Dark mode by default is acceptable but must be implemented via the chosen UI layer.

### 3.2. Sidebar (Main Sections)

Minimal screen set (corresponds to `admin-console-minimal-spec.md`):
- `Dashboard`
- `Network Graph`
- `Integrity`
- `Incidents`
- `Participants`
- `Config`
- `Feature Flags`
- `Audit Log`
- `Events` (timeline)

Optional (if enabled in Hub):
- `Equivalents` (dictionary management)
- `Transactions` / `Clearing` (global lists)

### 3.3. Header
- Breadcrumbs.
- Hub status indicator (minimum: successful root `/health` or equivalent aggregated status).
- Current role/account.
- Logout.

---

## 4. Screens (Requirements)

### 4.1. Dashboard (read-only)

Goal: quick status overview.

Must show:
- Version/environment/uptime (if available in health/metrics).
- Brief KPIs (minimum one screen without deep filters).

States:
- Loading / Error / Empty (if metrics unavailable).

### 4.2. Network Graph

Goal: trust network visualization.

Functions:
- Zoom/Pan.
- Search node by PID.
- Tooltip on edge: `limit`, `debt/used`, `available` (if available in data).
- Filter by equivalent.

### 4.3. Integrity Dashboard

Goal: invariant visibility and check triggering.

UI:
- Checks table: `name`, `status`, `last_check`, `details`.
- Button "Run Full Check" → confirmation → run.

### 4.4. Incidents (Incident Management)

Goal: operational actions on stuck transactions.

UI:
- List of "stuck" transactions (definition: intermediate status + SLA age exceeded).
- Action: `Force Abort` with mandatory reason input.

### 4.5. Participants

Goal: participant moderation/operations.

UI:
- Search by PID.
- Actions: Freeze/Unfreeze (with reason).
- For `auditor` — view only.

### 4.6. Config

Goal: runtime config viewing and modification.

UI:
- Table "key → value → description/default/constraints".
- Edit runtime subset only (at least — warning if key is not runtime).
- After `PATCH` show list of `updated[]`.

### 4.7. Feature Flags

UI:
- Toggles:
  - `feature_flags.multipath_enabled`
  - `feature_flags.full_multipath_enabled`
  - `clearing.enabled` (from `clearing.*` section, displayed as "Clearing enabled")
- For experimental (e.g., `full_multipath_enabled`) — warning.

Note: `clearing.enabled` is technically in `clearing.*` config section (see `config-reference.md`), but displayed with feature flags for UI convenience.

### 4.8. Audit Log

UI:
- Paginated table: `timestamp`, `actor`, `role`, `action`, `object`, `reason`.
- Detailed record panel: `before_state`/`after_state`.

### 4.9. Events (timeline)

UI:
- Filters: `event_type`, `actor_pid`, `tx_id`, `run_id`, `scenario_id`, date range.
- Table/timeline.

### 4.10. Equivalents (MVP)

Goal: equivalent dictionary management (included in MVP per `admin-console-minimal-spec.md` §3.4).

UI:
- Table: `code`, `description`, `precision`, `is_active`.
- Actions:
	- Create
	- Edit
	- Activate/Deactivate

Requirements:
- Any changes must go to audit-log.

### 4.11. Transactions (optional / Phase 2)

Goal: operational overview of all transaction types.

UI:
- Filters: `tx_id`, `initiator_pid`, `type`, `state`, `equivalent`, date range.
- Table: `tx_id`, `type`, `state`, `initiator_pid`, `created_at`.
- Details: `payload`, `error`, `signatures`.

### 4.12. Clearing (optional / Phase 2)

Goal: separate list of clearing transactions.

UI:
- Filters: `state`, `equivalent`, date range.
- Table/details: same as Transactions.

### 4.13. Liquidity analytics (optional / Phase 2)

Goal: aggregated charts/tables on liquidity and clearing efficiency.

UI:
- Filters: `equivalent` (optional), date range.
- Views:
	- summary (KPI)
	- series (time-series)

---

## 5. Global State and API Client

### 5.1. Pinia stores (minimum)
- `useAdminAuthStore`: token, role, user info.
- `useAdminConfigStore`: config + last updated keys.
- `useAdminGraphStore`: graph data by equivalent.

### 5.2. API client
- Base URL: `/api/v1`.
- Authorization: `Bearer`.
- Unified envelope processing `{success,data}` and errors `{success:false,error:{code,message,details}}`.

### 5.3. Session and Token Storage (Normative)
- Admin console must work over TLS only.
- Tokens must be stored in memory (runtime). Persistent storage (localStorage) is not an MVP requirement.
- On `401` UI must transition user to "login required" state.
- On `403` UI must show "Insufficient permissions" and not retry request.

---

## 6. API Mapping (Screen → Endpoint → Fields → Errors)

Note: detailed contracts must match `api/openapi.yaml`. If endpoint is missing in OpenAPI — it is considered a backend requirement and must be added to contract.

### 6.1. Config
- `GET /admin/config` → configuration key object.
- `PATCH /admin/config` → `{updated: string[]}`.

UI Rules:
- Editing allowed only if role permits (minimum: `admin`/`operator`).
- After successful `PATCH` UI updates config table and displays list of updated keys.

### 6.2. Feature Flags
- `GET /admin/feature-flags`
- `PATCH /admin/feature-flags`

Note: endpoint returns/accepts `multipath_enabled`, `full_multipath_enabled`, `clearing_enabled`. Parameter `clearing_enabled` technically corresponds to `clearing.enabled` in config, but unified with feature flags for UI convenience.

UI Rules:
- Any change operation must require explicit confirmation (minimum: confirm dialog).

### 6.3. Participants
- `POST /admin/participants/{pid}/freeze` (body: `{reason}`)
- `POST /admin/participants/{pid}/unfreeze` (body: `{reason?}`)
- `POST /admin/participants/{pid}/ban` (body: `{reason}`)
- `POST /admin/participants/{pid}/unban` (body: `{reason}`)

UI Rules:
- `reason` mandatory for freeze.
- After action UI shows toast + records fact in local UI log (does not replace audit log).

### 6.4. Audit Log / Events
- `GET /admin/audit-log` (paginated)
- `GET /admin/events` (paginated)

Fields (display guidelines, aligned with `api/openapi.yaml`):
- AuditLogEntry: `id`, `timestamp`, `actor_id`, `actor_role`, `action`, `object_type`, `object_id`, `reason`, `before_state`, `after_state`, `request_id`, `ip_address`.
- DomainEvent: `event_id`, `event_type`, `timestamp`, `actor_pid`, `tx_id`, `run_id`, `scenario_id`, `payload`.

### 6.5. Graph / Integrity / Incidents
- `GET /admin/analytics/graph?equivalent={code}`
- `GET /admin/integrity/status`
- `POST /admin/integrity/check`
- `POST /admin/transactions/{tx_id}/abort` (body: `{reason}`)

UI Rules:
- Graph: `equivalent` filter mandatory.
- Integrity check: action must require confirmation.
- Abort: `reason` mandatory; after abort UI should suggest going to Events and checking related `tx_id`.

### 6.6. Equivalents (optional / Phase 2)
- `GET /admin/equivalents` (query: `include_inactive`)
- `POST /admin/equivalents` (body: AdminEquivalentUpsert)
- `PATCH /admin/equivalents/{code}` (body: AdminEquivalentUpsert)

### 6.7. Transactions / Clearing (optional / Phase 2)
- `GET /admin/transactions` (paginated)
- `GET /admin/transactions/{tx_id}`
- `GET /admin/clearing` (paginated)

### 6.8. Liquidity analytics (optional / Phase 2)
- `GET /admin/analytics/stats`

---

## 7. UI State Matrix (Normative)

Mandatory for every screen:
- Loading (skeleton/spinner)
- Error (with retriable CTA)
- Empty (explanatory message)

Minimal texts:
- `403`: "Insufficient permissions to view this section"
- `401`: "Session expired. Please sign in again"

---

## 8. Generation Prompts (AI)

> "Create Vue 3 component for GEO Hub Admin on Element Plus (script setup). Component: [Screen Name]. Implement loading/empty/error, data extraction from envelope {success,data}, 401/403 handling. Endpoint(s): [list]."