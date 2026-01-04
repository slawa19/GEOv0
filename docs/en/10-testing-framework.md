# GEO Hub v0.1 — Testing Framework Spec (pytest e2e + artifacts + domain events)

**Version:** 0.1  
**Status:** draft (captures team decisions)  
**Goal:** define minimal specification to:
- manually and automatically run MVP scenarios (TS-01…TS-23);
- obtain traceable, reproducible run artifacts;
- have human-readable event logs and investigation capability via SSR admin console.

Related documents:
- Canonical API contract: [`docs/en/04-api-reference.md`](docs/en/04-api-reference.md)
- E2E scenarios: [`docs/en/08-test-scenarios.md`](docs/en/08-test-scenarios.md)
- Minimal admin console: [`docs/en/admin-console-minimal-spec.md`](docs/en/admin-console-minimal-spec.md)
- Configuration registry (runtime vs restart): [`docs/en/config-reference.md`](docs/en/config-reference.md)
- OpenAPI (must be aligned with canonical spec): [`api/openapi.yaml`](api/openapi.yaml)

---

## 1. Canonical API Contract (source of truth)

### 1.1. Canon
Canonical API contract = [`docs/en/04-api-reference.md`](docs/en/04-api-reference.md).

### 1.2. Base URL
Base URL = `/api/v1` (see [`docs/en/04-api-reference.md`](docs/en/04-api-reference.md)).

### 1.3. Response Format (envelope)
All REST endpoints (public and test-only) return envelope:

**Success:**
```json
{
  "success": true,
  "data": { }
}
```

**Success (paginated):**
```json
{
  "success": true,
  "data": [],
  "pagination": { "total": 0, "page": 1, "per_page": 20, "pages": 0 }
}
```

**Error:**
```json
{
  "success": false,
  "error": {
    "code": "E002",
    "message": "Insufficient capacity",
    "details": { }
  }
}
```

### 1.4. Swagger UI / ReDoc
- `/api/v1/docs`, `/api/v1/redoc`
- OpenAPI YAML: [`api/openapi.yaml`](api/openapi.yaml)

---

## 2. Testing Decision (MVP)

**Adopted:**
- SSR admin console (inside backend) for operator tasks.
- pytest e2e — main "scenario runner".
- DEV/TEST-only endpoints `/api/v1/_test/*` for faster setup/teardown.
- Domain events stored:
  - in DB (`event_log`) — for admin queries, investigation, and reliability;
  - exported to JSONL as run artifacts.

**Key rule:** test-only endpoints are used for **setup/teardown and diagnostic snapshots**, but core actions (e.g., `POST /payments`) should be tested through public API whenever possible.

---

## 3. Correlation (required identifiers)

### 3.1. What we correlate
To link:
- HTTP requests/responses,
- domain transactions (`tx_id`),
- events in DB,
- JSONL in artifacts,
- SSR admin console screens,

we use 4 identifiers:

- `run_id` — UUID of run (pytest session)
- `scenario_id` — scenario identifier (e.g., `TS-12`)
- `request_id` — UUID per HTTP request
- `tx_id` — UUID of domain transaction (if applicable)

### 3.2. Headers
Introduce headers (normative):

- `X-Run-ID: <uuid>` (optional, but required for e2e)
- `X-Scenario-ID: <string>` (optional, but required for e2e)
- `X-Request-ID: <uuid>` (if missing — server generates and returns)

Recommendation: server always returns `X-Request-ID` in response headers (echo / generated).

### 3.3. Propagation to events
When writing domain event to `event_log`, include:
- `run_id`, `scenario_id`, `request_id`, `tx_id`, `actor_pid` (if present).

---

## 4. Domain Events (minimal vocabulary)

### 4.1. Event Types (MVP)
Minimal event vocabulary covering TS-01…TS-23 from [`docs/en/08-test-scenarios.md`](docs/en/08-test-scenarios.md):

- `participant.created`
- `participant.frozen`
- `participant.unfrozen`
- `trustline.created`
- `trustline.updated`
- `trustline.closed`
- `payment.committed`
- `payment.aborted`
- `clearing.executed`
- `clearing.skipped`
- `config.changed`
- `feature_flag.toggled`

### 4.2. Canonical Event Format (for JSONL and DB)
```json
{
  "event_id": "uuid",
  "event_type": "payment.committed",
  "timestamp": "2025-12-22T14:30:00Z",

  "run_id": "uuid",
  "scenario_id": "TS-12",
  "request_id": "uuid",
  "tx_id": "uuid",
  "actor_pid": "alice_pid",

  "payload": {
    "equivalent": "UAH",
    "amount": "100.00",
    "routes": []
  }
}
```

---

## 5. Event Storage (DB)

### 5.1. Table `event_log` (minimum)
Recommended schema (PostgreSQL):

```sql
CREATE TABLE event_log (
    id          BIGSERIAL PRIMARY KEY,
    event_id    UUID NOT NULL,
    event_type  VARCHAR(64) NOT NULL,
    event_data  JSONB NOT NULL,

    run_id      UUID,
    scenario_id VARCHAR(32),
    request_id  UUID,
    tx_id       UUID,
    actor_pid   VARCHAR(128),

    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_event_log_event_type ON event_log(event_type);
CREATE INDEX idx_event_log_actor_pid  ON event_log(actor_pid);
CREATE INDEX idx_event_log_tx_id      ON event_log(tx_id);
CREATE INDEX idx_event_log_request_id ON event_log(request_id);
CREATE INDEX idx_event_log_run_scn    ON event_log(run_id, scenario_id);
```

### 5.2. Retention
For PROD: retention policy defined separately (e.g., 90–365 days, depending on audit requirements).  
For DEV/TEST: can be fully cleared on `/_test/reset`.

---

## 6. Test-only API (DEV/TEST ONLY)

### 6.1. General Security Requirements
- Test-only routes must exist **only** in `dev/test` environments.
- In `prod`:
  - either routes are not registered at all,
  - or guard checks and configuration prohibition are enabled that cannot be bypassed.

### 6.2. Endpoints (MVP)

#### POST `/api/v1/_test/reset`
Purpose: clear DB and Redis (baseline).

Response:
```json
{"success": true, "data": {"reset": true}}
```

#### POST `/api/v1/_test/seed`
Purpose: fast setup of typical topologies.

Request:
```json
{
  "scenario": "triangle",
  "params": {},
  "seed": "optional-seed"
}
```

Response:
```json
{"success": true, "data": {"summary": {}}}
```

List of allowed `scenario` values is fixed in implementation (and documented).

#### GET `/api/v1/_test/snapshot?include_events=true&run_id=...&scenario_id=...`
Purpose: get state snapshot for assertions and artifacts.

If `include_events=true`, also return events from `event_log` filtered by:
- `run_id` + `scenario_id` (if provided),
- otherwise — by `request_id` of current context (best-effort).

Response (structure of `data` can be flexible for MVP, but keys should be stabilized):
```json
{
  "success": true,
  "data": {
    "participants": [],
    "trustlines": [],
    "debts": {},
    "payments": [],
    "events": [
      {"event_type": "payment.committed", "...": "..."}
    ]
  }
}
```

---

## 7. pytest e2e Run Artifacts

### 7.1. Recommended Structure
```
tests/artifacts/
  <run_id>/
    meta.json
    TS-05/
      scenario_params.json
      requests/
      responses/
      snapshot.json
      events.jsonl
    TS-12/
      ...
```

### 7.2. What to Save (required)
- `scenario_params.json` — test input parameters (seed, amounts, equivalents)
- `requests/` and `responses/` — all HTTP exchanges (at minimum public endpoints)
- `snapshot.json` — result of `/_test/snapshot?include_events=true`
- `events.jsonl` — events from snapshot (each line = JSON object)

---

## 8. pytest Conventions (recommendations)

### 8.1. run_id / scenario_id
- `run_id` — session-level fixture (UUID)
- `scenario_id` — marker `@pytest.mark.scenario("TS-12")` or derivation from test name

### 8.2. HTTP Client
Client (httpx) must:
- add `X-Run-ID`, `X-Scenario-ID`
- add/generate `X-Request-ID` (or rely on server middleware)
- log request/response to artifacts

### 8.3. Selective Execution
Example:
```bash
pytest -k TS_12
```

---

## 9. SSR Admin Console: Domain Events Timeline

Based on [`docs/en/admin-console-minimal-spec.md`](docs/en/admin-console-minimal-spec.md), add screen:

**Domain Events / Timeline**
- Filters:
  - `event_type`
  - `actor_pid`
  - `tx_id`
  - `run_id` (for tests)
  - `scenario_id` (for tests)
  - date range
- Table:
  - `timestamp | event_type | actor | tx_id | short_summary`
- Details:
  - raw JSON payload

---

## 10. MVP+ (optional)

### 10.1. `/_test/time-travel`
Purpose: speed up time-dependent scenarios (TS-03, TS-17, TS-18) without real-time waiting.

### 10.2. `/_test/inject-fault`
Purpose: reproducibility of concurrent/error scenarios (e.g., TS-23) through controlled delays/errors.
