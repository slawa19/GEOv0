# GEO Hub — Config Reference (Parameter Registry)

This document is the **single source of truth** for GEO Hub MVP configuration parameters: purpose, allowed values, defaults, and risks.

Related documents:
- Protocol specification (including multipath/full multipath): [`docs/en/02-protocol-spec.md`](docs/en/02-protocol-spec.md)
- Deployment and configuration schema (env + YAML): [`docs/en/05-deployment.md`](docs/en/05-deployment.md)
- Minimal admin console for parameter management: [`docs/en/admin-console-minimal-spec.md`](docs/en/admin-console-minimal-spec.md)

---

## 1. General Principles

### 1.1. Two Configuration Levels

1) **Environment variables (.env)** — infrastructure/secrets/integrations (DB, Redis, keys, etc.).  
2) **Hub YAML config** — protocol and behavior parameters (routing/clearing/limits/flags/observability).

In current documents some parameters may appear as env variables (e.g., limits/timeouts). For MVP, **YAML config is canonical**, env is for infrastructure and secrets only.

### 1.2. Runtime vs Restart/Migration

- **Runtime (via admin console)**: can be changed at runtime without restart (with mandatory audit). Typically: `feature_flags.*`, `routing.*`, `clearing.*`, `limits.*`, `observability.*`.
- **Restart required**: change requires process/pod restart. Typically: `protocol.*` (protocol timeouts) and some `security.*`.
- **Migration required**: change requires migrations/state compatibility check. Typically: `database.*` and some `integrity.*` (if affects format/storage).

---

## 2. Parameter Table (by section)

Below: **purpose / values / default / application mode / impact and risks**.

---

## 2.1. `feature_flags.*` (runtime)

### `feature_flags.multipath_enabled`
- Purpose: enable multi-path payment splitting (if `false`, routing tries to find 1 path).
- Values: `true|false`
- Default: `true`
- Application: runtime
- Risks: disabling worsens payment throughput in fragmented networks.

### `feature_flags.full_multipath_enabled`
- Purpose: enable experimental **full multipath** (for benchmarks).
- Values: `true|false`
- Default: `false`
- Application: runtime
- Risks: may sharply increase routing cost; enable only with configured budget/timeouts and metrics.

---

## 2.2. `routing.*` (runtime)

### `routing.multipath_mode`
- Purpose: selected multipath mode.
- Values: `limited|full`
- Default: `limited`
- Application: runtime
- Risks: `full` — experimental; must be limited by budget/timeouts. Recommended to enable `full` only together with `feature_flags.full_multipath_enabled`.

### `routing.max_path_length`
- Purpose: upper bound on path length (hops) for routing.
- Values: `1..12` (practical: `3..8`)
- Default: `6`
- Application: runtime
- Risks: increasing value raises search cost and worsens explainability.

### `routing.max_paths_per_payment`
- Purpose: maximum paths used for splitting one payment.
- Values: `1..10`
- Default: `3`
- Application: runtime
- Risks: increasing value raises number of 2PC participants and probability of timeouts/abort; needed for perf checks.

### `routing.path_finding_timeout_ms`
- Purpose: total timeout for route finding for payment.
- Values: `50..5000`
- Default: `500`
- Application: runtime
- Risks: too low → many rejections; too high → p99 latency growth and load.

### `routing.route_cache_ttl_seconds`
- Purpose: TTL for routing results cache.
- Values: `0..600`
- Default: `30`
- Application: runtime
- Risks: high TTL with rapidly changing graph can give stale routes and extra aborts.

### `routing.full_multipath_budget_ms`
- Purpose: additional time/cost budget for `full` mode.
- Values: `0..10000`
- Default: `1000`
- Application: runtime
- Risks: increasing budget may overload CPU and worsen tail latencies.

### `routing.full_multipath_max_iterations`
- Purpose: iteration limit for max-flow-like implementations (if used).
- Values: `0..100000`
- Default: `100`
- Application: runtime
- Risks: high limit → unpredictable time.

### `routing.fallback_to_limited_on_full_failure`
- Purpose: if `full` doesn't fit in budget/timeout, allow fallback to `limited`.
- Values: `true|false`
- Default: `true`
- Application: runtime
- Risks: may hide `full` mode problems; requires `budget_exhausted` metrics.

---

## 2.3. `clearing.*` (runtime)

### `clearing.enabled`
- Purpose: enable clearing.
- Values: `true|false`
- Default: `true`
- Application: runtime
- Risks: disabling breaks key GEO value (debt growth, worse liquidity).

### `clearing.trigger_cycles_max_length`
- Purpose: maximum cycle length for **triggered** search after transaction.
- Values: `3..6` (for MVP recommended `3..4`)
- Default: `4`
- Application: runtime
- Risks: increasing to `5..6` can sharply raise search cost; parameter needed for perf checks and must be protected by time/candidate limits.

### `clearing.min_clearing_amount`
- Purpose: minimum clearing amount (filter "noise" cycles).
- Values: `0..(depends on equivalent)`
- Default: `0.01`
- Application: runtime
- Risks: too low → many small operations; too high → miss useful clearings.

### `clearing.max_cycles_per_run`
- Purpose: limit clearing transactions per run.
- Values: `0..100000`
- Default: `200`
- Application: runtime
- Risks: high limit → peak load/locks; low → slow debt "discharge".

### `clearing.periodic_cycles_5_interval_seconds`
- Purpose: period for background search of length-5 cycles (if enabled).
- Values: `0..604800` (0 = disabled)
- Default: `3600`
- Application: runtime
- Risks: frequent runs may compete with payments for resources.

### `clearing.periodic_cycles_6_interval_seconds`
- Purpose: period for background search of length-6 cycles (if enabled).
- Values: `0..604800` (0 = disabled)
- Default: `86400`
- Application: runtime
- Risks: as above, but higher cost.

---

## 2.4. `limits.*` (runtime)

Product/operational limits. Important: limits should account for `verification_level` (if used) and equivalents.

### `limits.max_trustlines_per_participant`
- Purpose: upper bound on trust lines per participant.
- Values: `0..10000`
- Default: `50`
- Application: runtime
- Risks: high limit increases graph size and routing/clearing load; low limit may worsen UX.

### `limits.default_trustline_limit.*`
- Purpose: starting trust line limit (if system supports auto-default).
- Values: number ≥ 0 (by equivalent type)
- Default: `fiat_like: 100`, `time_like_hours: 2`
- Application: runtime
- Risks: too high defaults increase default and conflict risk in pilot.

### `limits.max_trustline_limit_without_admin_approval.*`
- Purpose: trust line limit cap without explicit admin approval.
- Values: number ≥ 0
- Default: `fiat_like: 1000`, `time_like_hours: 10`
- Application: runtime
- Risks: too high → abuse/spam; too low → admin bottleneck.

### `limits.max_payment_amount.*`
- Purpose: upper bound on payment amount.
- Values: number ≥ 0
- Default: `fiat_like: 200`, `time_like_hours: 4`
- Application: runtime
- Risks: high → increased risks and multipath cost; low → worse UX.

---

## 2.5. `protocol.*` (restart required)

Section `protocol.*` describes parameters affecting **protocol rules** (2PC/validation/deadlines) and usually requires restart.

### `protocol.prepare_timeout_ms`
- Purpose: timeout for PREPARE phase in 2PC.
- Values: `100..60000`
- Default: `3000`
- Application: restart
- Risks: too low → many aborts on network delays; too high → long locks.

### `protocol.commit_timeout_ms`
- Purpose: timeout for COMMIT phase in 2PC.
- Values: `100..60000`
- Default: `5000`
- Application: restart
- Risks: too low → commit failures; too high → resource hold-up.

### `protocol.lock_ttl_seconds`
- Purpose: TTL for prepare locks.
- Values: `10..600`
- Default: `60`
- Application: restart
- Risks: too low → locks expire before completion; too high → long blocking on failures.

### `protocol.max_clock_drift_seconds`
- Purpose: allowed clock drift between participants.
- Values: `0..60`
- Default: `5`
- Application: restart
- Risks: too low → false drift errors; too high → replay attack window.

---

## 2.6. `security.*` (restart/migration)

### `security.jwt_access_token_ttl_minutes`
- Purpose: access token lifetime.
- Values: `5..1440`
- Default: `60`
- Application: restart
- Risks: too short → frequent refreshes; too long → security window.

### `security.jwt_refresh_token_ttl_days`
- Purpose: refresh token lifetime.
- Values: `1..365`
- Default: `7`
- Application: restart
- Risks: too short → UX friction; too long → token compromise window.

### `security.challenge_ttl_seconds`
- Purpose: challenge lifetime for auth.
- Values: `60..600`
- Default: `300`
- Application: restart
- Risks: too short → auth failures; too long → replay window.

---

## 2.7. `observability.*` (runtime)

### `observability.log_level`
- Purpose: logging level.
- Values: `DEBUG|INFO|WARNING|ERROR`
- Default: `INFO`
- Application: runtime
- Risks: DEBUG in prod → performance impact and log volume.

### `observability.metrics_enabled`
- Purpose: enable Prometheus metrics endpoint.
- Values: `true|false`
- Default: `true`
- Application: runtime
- Risks: minimal; disabling loses observability.

### `observability.structured_logging`
- Purpose: enable JSON structured logging.
- Values: `true|false`
- Default: `true`
- Application: runtime
- Risks: none significant.

---

## 3. Configuration File Example

```yaml
feature_flags:
  multipath_enabled: true
  full_multipath_enabled: false

routing:
  multipath_mode: limited
  max_path_length: 6
  max_paths_per_payment: 3
  path_finding_timeout_ms: 500

clearing:
  enabled: true
  trigger_cycles_max_length: 4
  min_clearing_amount: 0.01
  max_cycles_per_run: 200

limits:
  max_trustlines_per_participant: 50
  max_payment_amount:
    fiat_like: 200
    time_like_hours: 4

protocol:
  prepare_timeout_ms: 3000
  commit_timeout_ms: 5000
  lock_ttl_seconds: 60

observability:
  log_level: INFO
  metrics_enabled: true
```
