# T1548 — a payment tx_id replay without a stored fingerprint is refused, not guessed equal

Repository `D:\www\projects\2025\GEOv0`, branch `claude/012-money-s1`. Read first:

1. `AGENTS.md` §1, §9 (canon only per named node), §19 (grep `### 19`, read that section).
2. In `specs/015-financial-core-verification/spec.md`: rows `T1548`, `T1523`, `T1549` (grep `^| \`T15(48|23|49)\``).
   The decision in `T1548` is the contract; do not reopen it.

## The defect, verified in code

`PaymentService._resolve_existing_payment` (`app/core/payments/service.py:232-318`) is the single idempotency policy for
both the lookup and the insert-race row. At `:247-249` it compares fingerprints **only when the stored row has one**:

```python
existing_fp = (existing_payload.get("idempotency") or {}).get("fingerprint")
if existing_fp is not None and existing_fp != request_fingerprint:
    raise ConflictException("tx_id already used for a different request")
```

A stored `PAYMENT` row with **no** fingerprint (written before fingerprints existed, or by any path that does not record
one) skips the comparison and falls through to `idempotent_hit` (`:312-318`): the replay is answered with the stored
result as if it were the same request — equality of the canonical payload is **guessed**, not established.

## Where this sits in the closure

Programme 015 is closing in five steps (section `F` of `## Порядок исполнения` in the spec; `AGENTS.md` §19.5 — grep
`### 19.5` and read it). `T1548` is step 2 and is a **class 1** fix under §19.5: narrow, **no new entity** (no table,
column, route, module or framework), **one** §15 review round on the fix-delta. Anything else you notice while doing it
is class 2: report it with evidence, do not fix it, do not widen the task.

## The decision — implement it

- A `tx_id` replay whose stored `PAYMENT` row has **no fingerprint** answers `409` (`ConflictException`, `E008`) with a
  distinct `details.reason` naming an unverifiable legacy identity (for example `"unverifiable_legacy_identity"`),
  **not retryable**. It performs **no money writes** and does not compare or guess payload equality.
- The existing result remains readable through the existing GET for that transaction — do not add a route.
- Placement: in `_resolve_existing_payment`, right where the fingerprint is read, **before** the perimeter check and the
  in-progress branch, so the rule is one policy for lookup and insert-race alike. Keep the existing order rationale in
  the comments at `:251-261` true.
- Fingerprinted rows: behaviour unchanged (same fingerprint → idempotent hit or in-progress 409; different → 409
  "different request").

## Check before building — callers and writers

- **Which writers create `PAYMENT` transactions without a fingerprint today?** Search every `Transaction(` / insert of
  type `PAYMENT` (payment service paths at `:384-460` internal/staged/simulator, `:515+` public; seeds; fixtures;
  migrations). If any **live** path still writes a fingerprint-less PAYMENT row, its own replays would start answering
  409 — **stop condition 1**; report the path.
- **Callers of the replay**: public payment route, internal staged path used by the simulator
  (`app/core/simulator/real_payments_executor.py` classification: a 4xx becomes `REJECTED`), recovery. Confirm how each
  would classify the new 409 and that none retries it.
- **Canon**: the public payment create operation — does it already declare `409` with `components.responses.Conflict`?
  If yes, code only. If not, **stop condition 2**.

## Stop conditions

1. A live application path writes fingerprint-less `PAYMENT` rows.
2. The change needs an `api/openapi.yaml` edit.
3. §19 question 1 has no concrete answer for anything beyond this decision.

## Acceptance — each a test

- Stored fingerprint-less PAYMENT row + replay with the same `tx_id` → `409`, reason as decided, not retryable; debts,
  journal and the transaction row unchanged (assert counts/amounts before and after).
- Same through the insert-race branch (the row appears between lookup and insert), if reachable in a test; otherwise say
  why it is covered by construction (single policy function).
- Fingerprinted replay, same payload → unchanged idempotent result; different payload → unchanged 409.
- GET of that transaction still returns the stored result.
- Mutation: restore `existing_fp is not None and` guessing → the legacy test goes red. Restore byte-exactly, sha256.

## Gates and discipline

Check free commit memory before each gate (under 2 GB a red result is not evidence). SQLite
`scripts/verify_local.ps1 -TaskSlug t1548 -BackendOnly`; full PostgreSQL gate on `geov0_test_t1548` at `127.0.0.1`
(`-BackendMarker postgres -BackendSelector tests/integration`, `GEO_TEST_USE_MIGRATED_SCHEMA=1`,
`GEO_TEST_ALLOW_DB_RESET=1`); `tests/contract` and the contract drift ratchet before/after. Ad-hoc SQLite runs use
`.local-run/test-runs/t1548/test.db`. Predict counts before each run; poll your runs in the foreground.

No commits, no `specs/` edits. Report in English: writers found, callers and their classification, canon check, diff
summary, gate counts, mutation result. If you cannot establish something, write "not established".
