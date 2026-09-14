# T1523 — a bounded acceptance matrix for payment idempotency and reliability, plus one real restart (DRAFT — dispatch only after T1549 and T1548 land)

Repository `D:\www\projects\2025\GEOv0`, branch `claude/012-money-s1`. Read first:

1. `AGENTS.md` §1, §9, §19 (grep `### 19`, read that section).
2. In `specs/015-financial-core-verification/spec.md` (huge — grep, do not read in full): row `T1523` (about `:3586`),
   the narrowing row (about `:2282`), rows `T1548`, `T1549`, and the closure records of `T1549` and `T1548`.
3. The inventory this brief is built on:
   `specs/015-financial-core-verification/closure-briefs/F4-t1523-inventory.md`.

## The decision — implement it, do not reopen it

A **bounded** matrix of real application paths, written as **added assertions** on existing tests where a test already
drives the path, and as new tests only where none does. Removed by decision: reserved states, a public
`Idempotency-Key` (the canonical identity is the signed `tx_id`), process restart as a Cartesian axis. **Kept: exactly
one real restart scenario.**

Every cell asserts the same **no-double-effect triple** where money could move: debt rows and amounts unchanged,
`transactions` row count for that `tx_id` unchanged, and debt-journal envelope/entry counts for that operation unchanged
(the journal is the evidence the inventory found almost never asserted on replay). Tables:
`debt_operations(kind, identity, tx_id, state, effect_count)` and
`debt_journal_entries(operation_id, amount_before, amount_after, delta)` (`app/db/journal_tables.py` around `:117-142`,
`:205-237`).

**Anti-vacuum premise, before the triple:** where the first payment moved money, assert first that it has **exactly one
`COMPLETED` envelope** and a **positive `effect_count` matching its entry rows**. Only then snapshot the triple and prove
the replay leaves all three snapshots unchanged — otherwise "unchanged" is satisfied by a payment that wrote nothing. Every PostgreSQL cell runs at the
application's SERIALIZABLE (after `T1549`); a READ COMMITTED variant exists only as a named counter-probe.

## The cells — about eight, no more

| # | Path × situation | Expected | Starting point (inventory) |
|---|---|---|---|
| 1 | `POST /payments` × same signed `tx_id` after COMMITTED (also after operator stop and after integrity hold, as parameters) | 200 COMMITTED, triple unchanged | `test_payments_idempotency.py:29`; `test_p015_t1544_operator_stop_refuses_money.py:171`; hold not covered |
| 2 | `POST /payments` × same `tx_id` after ABORTED | the stored ABORTED result, no money | not covered |
| 3 | `create_payment` × same `tx_id` while in progress (a state reached after prepare, not only `NEW`) | 409 E008 in progress, no second effect | `test_payment_idempotency_postgres.py:19` (NEW only, READ COMMITTED) |
| 4 | `POST /payments` × same `tx_id`, different payload | 409, triple unchanged | `test_payments_idempotency.py:89` (status only) |
| 5 | `create_payment` × insert race between lookup and insert, at SERIALIZABLE | one transaction, one debt effect, one envelope; the loser gets **409/E008** by one of two defined branches — a `23505` unique violation is re-read and answers "Payment with same tx_id is in progress" (`service.py` around `:879-895`), a real `40001` answers "State conflict" with `retryable=true`, `conflict_kind=database_concurrency` (`:896-925`). **Do not assume which**: record which branch the stand actually hit and assert that one, with a premise proving the race really happened. A 500 or a double effect is a defect → stop condition 1 | `test_payment_idempotency_postgres.py:130-179` |
| 6 | fingerprint-less stored row | 409 unverifiable, no money | **already built by `T1548`** — reference its test, add the triple if missing; do not duplicate |
| 7 | `create_payment` × the commit really landed, then a timeout **or** a database error before the response | COMMITTED, abort not called, effects once | `test_payment_timeouts.py:112` fakes the commit and applies no debt — replace the fake with a real commit; the DB-error branch (`service.py` around `:1103-1133`) is not covered |
| 8 | **Restart**: process A commits a payment and exits before responding; process B receives the same signed request | COMMITTED, triple unchanged | not covered anywhere |

Engine-level duplicate commit (`test_payment_commit_advisory_locks_postgres.py:702`) and clearing post-commit replay
(`test_clearing_commit_replay_postgres.py:588`) are **already covered** and are not touched. **The matrix is capped at
these eight cells by the closure of programme 015** (section `F` of `## Порядок исполнения`, `AGENTS.md` §19.5): no
ninth cell, no extra assertions on neighbouring tests.

**§19.5 applies to what the matrix finds.** A cell that reproduces a loss on the money path (money or debt moving not as
the protocol says) is class 1 — stop and report it with the reproduction (stop condition 1). Anything else it reveals —
a wrong status without a double effect, a missing log, a slow path — is class 2: record it in the report with evidence,
mark the cell's test `xfail(strict=True, reason="... deferred to specs/BACKLOG.md <date>")` if it cannot be green, and do
not fix it.

## Cell 8 — the restart, without production hooks

- Process A is a **real separate OS process** (`subprocess`), running application code against the PostgreSQL test
  database (it inherits the test URL). **The commit point, confirmed by review:** inside the child script, wrap
  `PaymentEngine.commit` so that it awaits the original commit (`app/core/payments/engine.py` around `:1764`) and then
  calls `os._exit(...)` **before control returns** to `PaymentService` (`app/core/payments/service.py` around `:1031`).
  The patch lives in the child script only. **No test-only hook in application code.**
- The signed request body is serialized and passed to both processes; verification uses the participant's persisted
  public key, so no server-side private key needs to be shared.
- Premise assertions: A's exit code shows it died at the patched point; the commit is durable (read from a third
  connection after A has exited); no response was produced.
- Process B is a **second OS process** as well — not a fresh application instance inside the test process (the recorded
  decision says "a new process"). It receives the **same signed request** and answers COMMITTED; the triple is unchanged.
- If stopping "after commit, before response" turns out to need a hook inside application code, stop — stop condition 2.

## Stop conditions — substantive

1. A cell exposes an application defect (a 500, a double effect, a wrong status for a replay) — reproduce minimally and
   report; do not fix application code without my decision.
2. Cell 8 needs a production code hook.
3. A cell needs more than the triple and its premises — i.e. the matrix starts growing a framework (§19).

## Discipline

Mutations per cell: remove the guarding behaviour or break the assertion's subject (e.g. make replay re-apply the flow)
and see the cell go red; restore byte-exactly, sha256 verified. Check free commit memory before every gate (under 2 GB a
red result is not evidence). Gates: SQLite `scripts/verify_local.ps1 -TaskSlug t1523 -BackendOnly`; full PostgreSQL on
`geov0_test_t1523` at `127.0.0.1`; `tests/contract`. Ad-hoc SQLite runs use `.local-run/test-runs/t1523/test.db`.
Predict counts; poll your runs in the foreground.

No commits, no `specs/`. Report in English: the matrix with each cell's test `path:line`, status, and mutation result;
any stop-condition evidence; gate counts; files touched. Write "not established" where you cannot tell.
