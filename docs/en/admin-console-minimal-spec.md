# GEO Hub — Minimal Admin Console Specification

Goal: describe the **minimum necessary** admin console for MVP to:
- manage parameters (config) and feature flags without direct DB access;
- support pilot operations (equivalents, participants, incidents);
- have audit trail for operator actions.

Related documents:
- Parameter registry and runtime vs restart/migration markers: [`docs/en/config-reference.md`](docs/en/config-reference.md)
- Protocol sections where multipath/clearing settings matter: [`docs/en/02-protocol-spec.md`](docs/en/02-protocol-spec.md)
- Deployment (including configuration schema): [`docs/en/05-deployment.md`](docs/en/05-deployment.md)

---

## 1. Principle: "Minimal Implementation"

### 1.1. UI Approach (simplest option)

Both options are allowed, choose the simplest for the team:

**Option A (recommended for MVP): server-rendered pages (SSR)**
- Implemented inside backend application (FastAPI + templates).
- Minimum frontend infrastructure.
- Lower auth/CSRF risks.

**Option B: simplest SPA**
- Single static bundle + admin API calls.
- Slightly more complex for auth/CSRF/build, but also acceptable.

In both options, the action protocol is the same: UI calls admin endpoints, server writes audit-log.

---

## 2. Authentication/Authorization (minimum)

### 2.1. Roles (minimal set)

- `admin` — full admin console access.
- `operator` — limited access: participants (freeze/ban), view transactions/clearing, view configuration, toggle feature flags.
- `auditor` — read-only: audit-log, transactions, health/metrics, config (read-only).

### 2.2. Security Requirements

- Admin console accessible only via separate path (e.g., `/admin`) and/or separate domain.
- TLS required.
- For SSR: CSRF protection for POST/PUT/DELETE.
- Sessions/tokens: reusing existing JWT is acceptable, but admin endpoint access must be checked by role.

---

## 3. Screen Set (minimal composition)

### 3.1. Dashboard (read-only)
- Hub status: version, uptime, environment (dev/prod), basic load.
- Quick links to sections.

### 3.2. Configuration and Parameters (mixed)
**Goal**: view current configuration and change runtime parameters.

Screen should support:
- viewing current values;
- hints: description/range/default (can load from [`docs/en/config-reference.md`](docs/en/config-reference.md) as static reference or embed minimally in backend);
- changing only runtime parameters (see section 4).

Formats:
- "table" mode (key → value);
- optionally: "raw YAML/JSON" mode for viewing only.

### 3.3. Feature Flags (mutable)
- Toggles for:
  - `feature_flags.multipath_enabled`
  - `feature_flags.full_multipath_enabled`
  - `clearing.enabled`
- Display warning: some flags are experimental (e.g., full multipath).

### 3.4. Equivalents (mixed)
- List of equivalents: code, description, precision/scale.
- Actions (mutable): create/edit/deactivate equivalent.
- Requirements: all changes logged to audit-log.

### 3.5. Participants (mixed)
- List of participants, filter by status.
- Participant card: PID, verification level (if used), statistics (read-only).
- Actions (mutable):
  - `freeze` (suspend operations),
  - `unfreeze`,
  - `ban`/`unban` (if provided by model).
- Requirements: any status change — via audit-log with reason.

### 3.6. Transactions (read-only)
- Search by `tx_id`, PID, type (`PAYMENT`, `CLEARING`, ...), status, date range.
- View details: payload, routes, signatures (if shown), event timeline.
- Actions: read-only only (in MVP).

### 3.7. Clearing Events (read-only)
- List of `CLEARING` transactions, filters.
- View: cycle, amount, consent mode, rejection reason (if any).
- Actions: read-only (in MVP).

### 3.8. Audit Log (read-only)
- Search by time, actor, action type, object.
- View event details (before/after).

### 3.9. Health and Metrics (read-only)
- Health endpoints (aggregation): `/health`, `/ready` and key dependencies (DB/Redis).
- Link to `/metrics` (if enabled) and brief KPIs: latency p95/p99, error rate.

---

## 4. What is Read-only vs What Can Be Changed

Canonical markers — in [`docs/en/config-reference.md`](docs/en/config-reference.md). For MVP we fix minimum:

### 4.1. Runtime Mutable (via admin console)
Allowed to change:
- `feature_flags.*`
- `routing.*` (important for perf checks: `routing.max_paths_per_payment`, `routing.multipath_mode`)
- `clearing.*` (important: `clearing.trigger_cycles_max_length`)
- `limits.*`
- `observability.*` (e.g., `log_level`)

### 4.2. Read-only (requires restart/migrations)
View only:
- `protocol.*`
- `security.*` (by default)
- `database.*`
- `integrity.*` (by default)

---

## 5. What Actions Must Be Logged (audit-log)

### 5.1. Required Events

Always logged:
- login/logout (or admin session issuance)
- change of any runtime parameters and feature flags
- create/edit/deactivate equivalents
- freeze/unfreeze/ban/unban participant
- any "compensating operations" (if added later)

### 5.2. Minimal Audit-log Event Schema

Recommended format (log + table in DB):
- `event_id`
- `timestamp`
- `actor` (user id / service)
- `actor_role`
- `action` (enum)
- `object_type` (config/feature_flag/participant/equivalent/...)
- `object_id`
- `reason` (required for freeze/ban and critical limit changes)
- `before` / `after` (diff)
- `request_id` / `ip` / `user_agent`

---

## 6. Minimal Admin API Endpoints (optional)

UI can be SSR and not require public admin API, but for testing convenience it's useful to fix minimal endpoint groups:

- `GET /admin/config` (read)
- `PATCH /admin/config` (update runtime subset)
- `GET /admin/feature-flags`
- `PATCH /admin/feature-flags`
- `GET /admin/participants`
- `POST /admin/participants/{pid}/freeze`
- `POST /admin/participants/{pid}/unfreeze`
- `POST /admin/participants/{pid}/ban`
- `POST /admin/participants/{pid}/unban`
- `GET /admin/equivalents`
- `POST /admin/equivalents`
- `PATCH /admin/equivalents/{code}`
- `GET /admin/transactions`
- `GET /admin/transactions/{tx_id}`
- `GET /admin/clearing`
- `GET /admin/audit-log`

All mutating endpoints must write to audit-log.

---

## 7. MVP Limitations (explicit)

To avoid overcomplication:
- no "manual debt/transaction editing" in admin console;
- no complex RBAC constructor — only fixed roles;
- no full "YAML config editor" with schema validation — only table of keys and limited field set.
