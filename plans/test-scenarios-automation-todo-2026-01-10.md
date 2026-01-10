# GEO v0.1 — Test Scenarios: Automation TODO (2026-01-10)

Goal: make `docs/en/08-test-scenarios.md` executable as a reproducible pytest suite with clear pass/fail criteria and artifacts.

## Status matrix (TS → tests)

Legend: ✅ implemented, 🟡 partial, ❌ missing (as automation)

- TS-01 Register participant: ✅ covered by integration auth flows
- TS-02 Login + protected access: ✅ covered by integration auth flows
- TS-03 JWT expiry + refresh: ✅ covered by integration tests (refresh + expired access)
- TS-04 Participant search: ✅ covered by integration tests (`q/type/limit/page`)
- TS-05 Trustline create: ✅ covered
- TS-06 Trustline update: ✅ covered
- TS-07 Reduce limit below used: 🟡 add negative test (domain error)
- TS-08 Close trustline when used=0: ✅ covered
- TS-09 Close trustline when used>0: 🟡 add negative test
- TS-10 Capacity check: ✅ covered (integration)
- TS-11 Max-flow: 🟡 add deterministic max-flow assertions (small graph)
- TS-12 Single-path payment success: ✅ covered
- TS-13 Single-path insufficient capacity: ✅ covered (HTTP 400 + E002)
- TS-14 Multipath 2 routes: ✅ covered
- TS-15 Multipath limit (max_paths_per_payment): ✅ covered via admin-configured `ROUTING_MAX_PATHS`
- TS-16 Full multipath feature flag: 🟡 add test around `/admin/feature-flags` + max-flow metadata
- TS-17 Clearing length-3: ✅ covered
- TS-18 trigger_cycles_max_length / periodic: ✅ covered via `/clearing/*` max_depth automation
- TS-19 WebSocket events: ✅ MVP WS implemented + unit test for `payment.received`
- TS-20 Freeze participant blocks operations: ✅ covered via admin API integration test
- TS-21 Admin changes routing params: ✅ covered via `/admin/config` + payment behavior
- TS-22 Idempotency: ✅ covered
- TS-23 Concurrent payments on bottleneck: ✅ Postgres-only integration test for concurrent prepare on shared bottleneck; sqlite runs skip it

## Automation plan (minimal)

1) Keep `python -m pytest -q` green and fast (<15s locally).
2) Add a smoke subset matching Top-5: TS-01/05/12/14/17.
3) Add an extended subset for TS-03/04/15/20/21/23.
4) Add artifacts/snapshots only if/when `/_test/*` endpoints are implemented; until then, assert via API responses and DB invariants.
