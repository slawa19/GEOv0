# Phase 3 REST/SSE contract map

Status: **COMPLETE** (2026-08-08). This is the dated evidence ledger for
REN-009 and REN-012B1 at base
`cf76e096ddaf9b896de161a5a44e208f982484fd`. It does not replace
`api/openapi.yaml`, backend schemas, code, or fresh gate results.

The accepted product HEAD is
`f8517bc90b119a3b156de0a2945019d1b9381118`. Runtime remediation ended at
`032ed86b70eb3b33be53224a6b42949f5d77a052`; the final commit only aligns the
three affected Playwright mocks with the selected canonical contracts. Published
branch evidence is accepted below. No Phase 4 work has started.

## Frozen boundary

Phase 3 owns only the selected Admin mutation/config responses, critical
Simulator real-mode REST responses, SSE ingress acceptance, and the OpenAPI
schemas needed to protect those paths. Admin loader ownership and operator UI
flows remain Phase 4; replay/state/effect extraction remains Phase 5. Health and
dashboard presentation blobs, auth redesign, config empty-patch semantics, wire
versioning, and unrelated schema cleanup are not part of this phase.

## Current / intended / optimal decisions

| Contract family | Current behavior at the Phase 3 base | Intended behavior | Optimal target and decision |
|---|---|---|---|
| Admin config | The real client flattened an unvalidated `{items}` response; the mock returned a separately shaped flat fixture. | The existing flat UI facade stays stable, while malformed wire/mock data is rejected explicitly. | **FIX** one shared decoder for wire GET/PATCH plus the retained flat facade. |
| Admin feature flags | The real PATCH performed GET-plus-full-state write and real/mock responses were unvalidated; generated mock data exposed obsolete `audit_log_enabled`. | PATCH is partial and the response is the canonical complete three-boolean state. | **FIX** direct partial PATCH, one shared strict response decoder, and generator-owned fixture parity. Landed in `72e4d9c` and `64262c7`. |
| Admin participant mutation and transaction abort | Typed calls trusted successful JSON without runtime validation; canonical success schemas were generic. | Freeze/unfreeze and abort accept only their exact durable result shapes. | **FIX** shared real/mock decoders and exact OpenAPI success schemas; no change to mutation ownership. |
| Admin equivalent mutation | Runtime create/update inputs accepted codes and precision outside the canonical Equivalent bounds; successful mutation responses were trusted casts. | Request validation and selected success payloads retain the canonical code/precision and response contracts. | **FIX** input bounds landed in `d71f8a6`; shared decoders and exact success schemas landed in `d0e490b`/`dffbe35`. Review then exposed SQLite-naive response timestamps and mock request normalization; `9d8a69c` attaches UTC at the wire schema, pins request bounds, and rejects invalid mock requests before mutation. Claude found a sibling scenario writer and legacy-row read failure; `40f43ee` keeps mutation `Equivalent` strict, validates active writers, and exposes pre-contract rows through explicit `StoredEquivalent` read schemas. Invalid legacy codes remain visible but require deliberate manual cleanup; no automatic rename/migration is justified. |
| Admin integrity actions | Status, verify, and repair results crossed the frontend boundary as broad records, with mock drift on verify. | Operator-significant integrity results are decoded identically by real and mock clients. | **FIX** only selected response parity; auth-policy and verify-extra-policy observations remain ledger-only. |
| Simulator scenario list/detail, run status, snapshot | Generic HTTP helpers returned erased TypeScript casts, so malformed 2xx data could enter trusted real-mode state. | These four critical responses are validated at endpoint ingress against actual backend shapes. | **FIX** one explicit REST contract-error path; no validation expansion to actions or presentation-only responses. |
| Simulator known SSE families | The normalizer silently skipped malformed collection members and allowed partially valid events to mutate state/effects. | Lifecycle/status, topology, payment, and clearing events are accepted atomically or diagnosed and ignored. | **FIX** strict whole-event acceptance before cursor, dedup, state, stats, patches, or FX. Landed in `109f6d8`. |
| Simulator unknown SSE families | Unknown types were cast to the catch-all `SimulatorEvent` and reached the trusted callback path. | Unknown input is observable but non-mutating until a consumer contract is intentionally added. | **FIX** diagnostic ignore. `audit.drift` remains a producer event but has no Phase 3 UI consumer. |
| SSE frame/payload identity | The frame cursor advanced before JSON/schema acceptance and frame `id` was not compared with payload `event_id`. | Only an accepted event with matching identifiers can advance replay state. | **FIX** exact ID match when a frame ID is present; malformed/unknown input leaves cursor and dedup untouched. Landed in `109f6d8`. |
| SSE context and timestamps | An otherwise valid event from another run/equivalent, or a non-canonical date-time string, could advance cursor and effects on the active connection. Topology payload extras were also silently discarded. | Accepted input must match the active connection and the exact selected producer schema before cursor, dedup, callbacks, or effects. | **FIX** strict topology nested keys, canonical ISO date-times, and pre-cursor run/equivalent binding landed in `cf693ad`. |
| SSE replay transport | Runtime and client used `Last-Event-ID` and handled strict-replay `410`, but the selected OpenAPI operation omitted both. | Canonical and generated OpenAPI describe the same replay request/response contract exercised by runtime. | **FIX** explicit FastAPI header injection with unchanged replay logic, canonical header/410 response, and mutation-sensitive guard landed in `cf693ad`. |

## Replay and stale-context characterization

| Case | Current mechanism and evidence | Phase 3 decision |
|---|---|---|
| Cursor | `connectSse` sends `Last-Event-ID`; the accepted-event boundary now advances `real.lastEventId` only after JSON, schema, and frame-ID checks. Backend replay uses per-run monotonic IDs and an in-memory bounded buffer. | **KEEP** in Phase 3. Process restart is best-effort by design; no durable journal is introduced. |
| Duplicate | `markEventProcessed(runId, event_id)` suppresses duplicate state/effect application; the existing replay-dedup component test observes one label/FX application for a duplicate. | **KEEP**, with accepted-event ordering protected by `109f6d8`. |
| Stale run | `404` resets the stale run context; refresh work carries sequence/context guards so a changed run cannot publish a delayed snapshot. | **KEEP** existing behavior and characterize with the affected component selectors; broader state ownership stays Phase 5. |
| Replay too old (`410`) | Strict replay is opt-in. Backend returns `410` when the cursor predates the retained ring; the client clears its cursor, refreshes status and snapshot, then reconnects. | **KEEP** recovery semantics, characterized in `e219dd8` and described by the canonical/generated operation in `cf693ad`. No strict-replay default change. |
| Replay ordering | Subscription queues buffered events, while stream setup publishes an initial status and may emit that status before prefetched older domain events. This can make reconnect delivery non-monotonic even though IDs themselves are monotonic. | **DEFER to Phase 5** replay/state seam unless a Phase 3 acceptance test proves a selected contract violation. Do not redesign the wire or runtime in this phase. |

## Explicit residuals outside the frozen boundary

- Existing rows with a noncanonical Equivalent code remain visible through
  `StoredEquivalent` but cannot be renamed through the selected mutation API.
  Automatic rename or data migration is unsafe without owner-selected mappings;
  cleanup is deliberate/manual if an audit finds such rows.
- `AdminAuditLogItem.timestamp` can still serialize a SQLite-naive database
  timestamp. Admin Audit Log loader/operator ownership is Phase 4, so this sibling
  is recorded there rather than expanding the selected Phase 3 Integrity surface.
- Non-monotonic replay delivery and replay/state/effect extraction remain Phase 5
  as recorded above.

## Owner slices

| Slice | Owner surface | Status |
|---|---|---|
| Feature-flag partial PATCH | `admin-ui/src/api/realApi.ts` and focused concurrency test | **ACCEPTED** — `72e4d9c` |
| Equivalent input parity | `app/schemas/admin.py`, OpenAPI contract guard, focused integration test | **ACCEPTED** — `d71f8a6` |
| Simulator SSE acceptance | normalizer plus real-mode ingress and focused tests | **ACCEPTED** — `109f6d8` |
| Admin config/feature flags | shared decoder, real/mock clients, canonical fixture generator/copies | **ACCEPTED** — `64262c7` |
| Remaining Admin selected decoders | frontend API contracts and focused tests | **ACCEPTED** — `d0e490b`, integration alignment `865c4b2` |
| Simulator critical REST decoders | frontend endpoint ingress and focused tests | **ACCEPTED** — `8494992`; reconnect characterization `e219dd8` |
| Selected OpenAPI response parity | canonical YAML, narrow backend schemas only if required, mutation-sensitive guards | **ACCEPTED** — `dffbe35` |
| Admin adversarial remediation | Equivalent timestamps/request parity and exact selected response guards | **ACCEPTED** — `9d8a69c`; independent re-review found no sustained P1/P2 |
| Simulator adversarial remediation | strict nested ingress, date-time/context binding, replay header/410 parity | **ACCEPTED** — `cf693ad`; independent cross-review found no sustained P1/P2 |
| Claude Simulator remediation | bounded diagnostic taxonomy for arbitrary untrusted event types | **ACCEPTED** — `9ce6338`; full Simulator unit/typecheck/build repeated |
| Claude backend remediation | Integrity UTC wire timestamps, active Equivalent writer guards, strict mutation versus legacy-visible read projection | **ACCEPTED** — `40f43ee`; independent delta review found no sustained P1/P2 |
| Claude fix-delta remediation | explicit legacy PATCH conflict, early scenario semantic validation, remaining selected Integrity UTC timestamps | **ACCEPTED** — `032ed86`; final internal adversarial review found no sustained P1/P2 |
| Published E2E contract-mock alignment | three selected scenario-list/run-status/snapshot Playwright mocks only | **ACCEPTED** — `f8517bc`; independent read-only review CLEAN, no product decoder or Phase 4 assertion changed |

## Candidate milestone evidence

All final commands below ran on the latest relevant product commit through
`f8517bc90b119a3b156de0a2945019d1b9381118`; exact per-surface SHAs are stated
where they differ.

- `\.\scripts\verify_local.ps1 -TaskSlug phase3_final_exact_backend -BackendOnly`
  exited `0` on `032ed86`: `774 passed`, `3 skipped`, `15 deselected`. The earlier combined
  `phase3_product_milestone` invocation timed out after 300 seconds during the
  backend-first step and produced no verdict; splitting the surfaces established
  that the backend suite itself requires about 6.5 minutes.
- `npm --prefix admin-ui run test` passed `113` tests, and the production build
  (including fixture sync/validation) exited `0`. Only the existing large-chunk
  warning remained; deterministic sync produced no tracked diff.
- Simulator typecheck exited `0`, `npm --prefix simulator-ui/v2 run test:unit`
  passed `668` tests, and the production build exited `0` on `9ce6338`; the
  following runtime commits are backend/OpenAPI-only and `f8517bc` changes only
  E2E mocks. Deterministic fixture sync produced no tracked diff; the existing
  dynamic/static import warning remained.
- The final OpenAPI contract selector passed `23` tests; strict SSE replay `410`
  passed `1` integration test. Focused Admin and Simulator remediation selectors
  are recorded with their owner commits above.
- Two initial adversarial reviews found one Admin P1 and seven P2 findings across
  Admin/OpenAPI and Simulator ingress. All were reproduced and remediated in
  `9d8a69c` and `cf693ad`; two independent read-only delta reviews reported no
  sustained P1/P2.
- Claude Code `2.1.226` reviewed exact range `cf76e096..cf693ad` from a clean
  credential-free standalone clone with `/code-review high`, `--model opus
  --effort high`, and read-only tools. The successful retry exited `0`, produced
  complete JSON with `is_error=false`, and resolved `claude-opus-5`. Its three
  findings were manually sustained: naive Integrity checkpoint timestamps,
  active/legacy Equivalent writer-reader incompatibility, and unbounded
  attacker-controlled diagnostic keys. They were fixed in `9ce6338` and
  `40f43ee`; the remediation delta received a clean internal adversarial review.
- The single Claude fix-delta review of exact range `cf693ad..40f43ee` used the
  same clone, model and read-only constraints. It exited `0`, produced complete
  JSON with `is_error=false`, and resolved `claude-opus-5`. Manual triage
  sustained an uncaught legacy PATCH validation failure, two selected Integrity
  timestamp siblings, and late scenario semantic validation; strict rejection
  of non-integer seed precision was retained as intentional fail-closed behavior.
  `032ed86` returns canonical/generated `409` before mutation/audit, validates all
  scenario equivalent sources before persistence/registration, and attaches UTC
  to the selected Integrity checksum/audit timestamps. No third external loop was
  opened; an independent read-only review of `40f43ee..032ed86` reported no
  sustained P1/P2, and focused remediation selectors passed `43`, `23`, and `15`
  tests before the full backend gate above.
- The first attempted full Claude invocation exceeded its parent runner timeout;
  its later orphan completion left empty result artifacts. It was classified
  **INVALID** and was not counted as review evidence. The clean retry and the
  fix-delta run described above are the two accepted external results.
- Published run `31265022918` on `032ed86` exposed one additional Simulator E2E
  failure: an affected test still mocked the pre-contract scenario-list shape.
  All three active E2E call-sites for selected scenario-list/run-status/snapshot
  responses were aligned without weakening the decoder. Local contract unit
  tests passed `10`, typecheck and Playwright discovery exited `0`; browser
  execution was unavailable locally because the pinned Chromium executable was
  absent. Independent read-only review of the three-file delta was CLEAN.
- `git diff --check` exits `0`. After the product commit, the working tree
  contained only this ledger before the four-file documentation closeout began.

## Published exit evidence

[Workflow run 31265705618](https://github.com/slawa19/GEOv0/actions/runs/31265705618)
completed on exact HEAD `f8517bc90b119a3b156de0a2945019d1b9381118`.

- Required local-equivalent gates passed: backend `775 passed`, `2 skipped`, `15
  deselected`; Alembic single head `017_add_owner_to_simulator_runs`; Admin `113`
  tests plus build; Simulator `668` tests plus typecheck/build.
- Disposable PostgreSQL passed the bounded matrix (`3 passed`) and full registered
  tier (`11 passed`, `130 deselected`). Production-like container/schema smoke,
  active Admin/Simulator Chromium smoke, and Simulator super-smoke (`3 passed`)
  also succeeded.
- The workflow conclusion is truthfully `failure`, not “CI green”. Admin E2E
  retained the exact prior `2 failed / 2 passed` graph baseline. Simulator visual
  E2E returned to the exact Phase 2 `6 failed / 13 passed` set: the five manual
  operation tests and stale-run first-load test. The transient extra
  scenario-switch failure from run `31265022918` is gone. The job still logs its
  pre-existing backend bootstrap failure (`ModuleNotFoundError: pydantic`) and
  resulting proxy refusal; these later-phase E2E owners are not Phase 3 contract
  regressions.

The Phase 3 exit conditions are satisfied: deliberate selected drift is guarded,
real/mock Admin paths share decoders, malformed or unknown Simulator ingress is
non-trusted/non-mutating, and canonical OpenAPI plus affected required frontend
gates pass. Phase 4 remains paused.
