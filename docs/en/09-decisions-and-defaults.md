# GEO v0.1 — Key Decisions and Defaults for MVP

**Version:** 0.1  
**Date:** December 2025

Summary of key architectural decisions and recommended defaults for MVP.

---

## 1. Key MVP Decisions

### 1.1. Architecture and Nodes

| Decision | MVP Choice |
|----------|------------|
| **Node model** | Hub-centric (participant = account in hub) |
| **Operation signing** | Required on client side (Ed25519) |
| **2PC coordination** | Hub server (state + transactional locks in DB) |
| **Primary client** | PWA (Web Client) |
| **Mobile/desktop** | Flutter — deferred to v1.0+ |

### 1.2. Clearing

| Decision | MVP Choice |
|----------|------------|
| **Mode** | Automatic (scheduled + triggered) |
| **Participant consent** | Default `auto_clearing: true` in TrustLine policy |
| **Cycle length (triggered)** | 3–4 nodes |
| **Cycle length (periodic)** | 5–6 nodes (optional) |

### 1.3. Routing

| Decision | MVP Choice |
|----------|------------|
| **Base mode** | Limited multipath (k-shortest paths) |
| **Max paths** | 3 |
| **Max hops** | 6 |
| **Full multipath** | Disabled by default (feature flag for benchmarks) |

### 1.4. Equivalents

| Decision | MVP Choice |
|----------|------------|
| **Who creates** | Admin/operator only |
| **Starter set** | UAH, HOUR, kWh |

### 1.5. Verification

| Decision | MVP Choice |
|----------|------------|
| **KYC** | Not implemented in MVP |
| **Verification levels** | 0–3 (limits and permissions by level) |
| **Moderation** | Manual (admin/operator) |

### 1.6. Operator Powers

| Decision | MVP Choice |
|----------|------------|
| **Actions** | freeze/unfreeze, ban/unban, investigation, compensating operations |
| **Audit** | Mandatory audit-log for all actions |
| **Roles** | admin, operator, auditor |

### 1.7. Transaction State Machine (internal)

The database model stores internal `Transaction.state` values for operational safety (recovery, idempotency, 2PC bookkeeping). These states are **not** the same as the public payment “result status”.

**Allowed internal states (DB constraint):** `NEW`, `ROUTED`, `PREPARE_IN_PROGRESS`, `PREPARED`, `COMMITTED`, `ABORTED`, `PROPOSED`, `WAITING`, `REJECTED`.

**PAYMENT (2PC-style, MVP implementation):**

| State | Meaning (MVP) |
|------|----------------|
| `NEW` | Transaction record created, routing computed and persisted in `payload` |
| `PREPARED` | Segment locks created successfully (phase 1 done) |
| `COMMITTED` | Debts updated and locks removed (phase 2 done) |
| `ABORTED` | Terminal failure; locks removed (best-effort) and `error` stored |

Notes:
- `ROUTED` and `PREPARE_IN_PROGRESS` exist as reserved/internal states but are not currently emitted by the MVP payment engine.
- `REJECTED` exists as a reserved terminal state for future “explicit reject” flows; MVP treats it as terminal.

**CLEARING (MVP):**

| State | Meaning (MVP) |
|------|----------------|
| `NEW` | Clearing transaction created |
| `COMMITTED` | Clearing applied successfully |
| `ABORTED` | Terminal failure |

**Recovery behavior (MVP):** transactions stuck in “active” internal states past the configured time budget are aborted and any associated locks are cleaned up.

**Public payment API status:** `PaymentResult.status` is a final outcome and only returns `COMMITTED` or `ABORTED`.

---

## 2. Defaults and Limits

### 2.1. 2PC Timeouts

| Parameter | Default | Range |
|-----------|---------|-------|
| `protocol.transaction_timeout_seconds` | 10 | 5–30 |
| `protocol.prepare_timeout_seconds` | 3 | 1–10 |
| `protocol.commit_timeout_seconds` | 5 | 2–15 |
| `protocol.lock_ttl_seconds` | 60 | 30–300 |

### 2.2. Routing

| Parameter | Default | Range |
|-----------|---------|-------|
| `routing.max_path_length` | 6 | 3–10 |
| `routing.max_paths_per_payment` | 3 | 1–10 |
| `routing.path_finding_timeout_ms` | 500 | 100–2000 |
| `routing.multipath_mode` | `limited` | limited, full |

### 2.3. Clearing

| Parameter | Default | Range |
|-----------|---------|-------|
| `clearing.enabled` | true | true/false |
| `clearing.trigger_cycles_max_length` | 4 | 3–6 |
| `clearing.periodic_cycles_max_length` | 6 | 4–8 |
| `clearing.min_clearing_amount` | 1.00 | 0.01–100 |
| `clearing.max_cycles_per_run` | 100 | 10–1000 |
| `clearing.trigger_interval_seconds` | 0 (immediate) | 0–60 |
| `clearing.periodic_interval_minutes` | 60 | 5–1440 |

### 2.4. Limits

| Parameter | Default | Range |
|-----------|---------|-------|
| `limits.default_trust_line_limit` | 1000.00 | 0–∞ |
| `limits.max_trust_line_limit` | 100000.00 | 1000–∞ |
| `limits.max_payment_amount` | 50000.00 | 100–∞ |
| `limits.daily_payment_limit` | 100000.00 | 1000–∞ |

### 2.5. Feature Flags

| Parameter | Default | Description |
|-----------|---------|-------------|
| `feature_flags.multipath_enabled` | true | Limited multipath enabled |
| `feature_flags.full_multipath_enabled` | false | Full mode for benchmarks |
| `feature_flags.inter_hub_enabled` | false | Inter-hub interaction |

---

## 3. Pilot Size (design target)

| Metric | Target Value |
|--------|--------------|
| **Participants** | 50–200 |
| **Transactions/day** | up to 1000 |
| **Peak load** | 5–10 tx/sec |
| **Equivalents** | 1–3 |

---

## Related Documents

- [config-reference.md](config-reference.md) — full parameter registry with descriptions
- [02-protocol-spec.md](02-protocol-spec.md) — protocol specification
- [03-architecture.md](03-architecture.md) — system architecture
- [admin-console-minimal-spec.md](admin-console-minimal-spec.md) — admin console specification
