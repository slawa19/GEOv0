# T1523 inventory — existing idempotency and reliability tests (read-only survey, 2026-09-14)

Produced by a read-only exploration of `D:\www\projects\2025\GEOv0` at HEAD `d13c66a`. Nothing was run, so every claim
about behaviour under SERIALIZABLE is inferred, not measured. Line numbers are as of that HEAD.

**Headline:** no test restarts a process. Payment replay tests are thin: none counts journal rows on replay.

**Spec pointers:** the T1523 narrowing row is at `specs/015-financial-core-verification/spec.md:2282` (related decision
and order at `:2207-2208`, `:2231`); the `T1523` row at `:3586-3590` names the strongest existing evidence as
`test_clearing_commit_replay_postgres.py:587,772` and the weakest as `tests/unit/test_payment_timeouts.py:182`.
`T1548` at `:806`, `T1549` at `:805`. The note behind `T1549` is `specs/BACKLOG.md:810-827`: the shared test engine runs
READ COMMITTED, and switching it "changes the behaviour of 136 Postgres tests".

**Two conditions that affect every Postgres row:**
- Test engine: sets no isolation level (`tests/conftest.py:109-114`); the application uses SERIALIZABLE
  (`app/config.py:68`, `app/db/session.py:69`).
- Test wrapping: on Postgres, `db_session` wraps each test in an outer transaction and turns each commit into a SAVEPOINT
  (`tests/conftest.py:382-395`). Tests opening their own `TestingSessionLocal()` commit for real. The `client` fixture
  does not run app startup/shutdown, so the recovery loop never runs in HTTP tests (`conftest.py:414-416`).

## 1. Payment idempotency and replay (`app/core/payments/service.py`)

Code: `_resolve_existing_payment` `:232-318`; first lookup `:662-673`; insert race `:879-895`; retryable insert error
`:896-925`.

| Situation | Test | path:line | Backend | Proves | Gaps |
|---|---|---|---|---|---|
| Same tx_id after COMMITTED | `test_payments_tx_id_returns_same_result` | `tests/integration/test_payments_idempotency.py:29` | default SQLite tier | 200 twice; same tx_id/status | debts, tx count, journal not checked |
| Same, with operator stop | `test_an_accepted_payment_still_replays_its_result_after_the_stop` | `tests/integration/test_p015_t1544_operator_stop_refuses_money.py:171` | SQLite HTTP | 200 COMMITTED after stop; debt (1 row, 10.00) | no journal; not Postgres |
| Same, with integrity hold | none | — | — | — | not covered (hold check after idempotency decision, `:689-698`) |
| Same tx_id after ABORTED | none | — | — | — | not covered (`test_payment_abort_has_error_code.py:15` only GETs) |
| While in progress | `test_concurrent_duplicate_payment_request_never_regresses_terminal_state_postgres` | `tests/integration/test_payment_idempotency_postgres.py:19` | Postgres, READ COMMITTED explicit (`:116-123`) | loser 409 E008 in progress; winner COMMITTED; 1 tx row, debt 10.00, no locks, 1 audit, 1 event | no journal; only NEW reached |
| Different payload | `test_payments_tx_id_reuse_with_different_payload_conflicts` | `tests/integration/test_payments_idempotency.py:89` | SQLite HTTP | 10 then 11 → 409 E008 | debt/journal unchanged not asserted |
| Different sender / non-PAYMENT (`:241-244`) | none | — | — | — | not covered |
| Fingerprint-less stored row (`:248`, T1548) | none | — | — | — | not covered; treated as match today |
| Insert race | same Postgres test | `test_payment_idempotency_postgres.py:130-179` | Postgres, READ COMMITTED | loser passes lookup, hits unique, re-reads, "in progress" | only valid at READ COMMITTED |
| Commit timeout after real commit | `test_payment_commit_timeout_returns_committed_when_tx_already_committed` | `tests/unit/test_payment_timeouts.py:112` | SQLite | returns COMMITTED, abort not called | prepare stubbed, commit faked as `UPDATE state` (`:186-194`), no debt applied |
| Prepare timeout | `test_payment_prepare_timeout_aborts_transaction` | `tests/unit/test_payment_timeouts.py:21` | SQLite | 504; ABORTED | stubbed; no replay |
| Engine commit/abort on COMMITTED | `test_commit_is_idempotent_when_already_committed`, `test_abort_is_noop_when_already_committed` | `tests/unit/test_payments_2pc.py:70`, `:97` | SQLite | engine idempotent | hand-seeded, no debts |
| Two concurrent commits | `test_concurrent_same_transaction_commit_applies_effects_once_postgres` | `tests/integration/test_payment_commit_advisory_locks_postgres.py:477` | Postgres, SERIALIZABLE (`:497-499`) | debt 8.00 once, no reverse, no locks, 1 audit | engine level |
| Same with journal (T1529) | `test_concurrent_duplicate_commit_is_idempotent_with_journal_history_postgres` | same `:702` | Postgres, SERIALIZABLE | debt 8.00, 1 audit, envelopes == ["COMPLETED"] (`:816`) | only payment journal assertion; engine level |
| Duplicate prepare / new prepare during abort / commit vs abort | tests at `:1122`, `:1248`, `:1006` | same file | Postgres, READ COMMITTED (`:1142`, `:1268`, `:1024`) | final states consistent | pinned READ COMMITTED |

## 2. Clearing idempotency (`_reconcile_committed_execution`, `app/core/clearing/service.py:331`)

| Test | path:line | Backend | Proves | Gaps |
|---|---|---|---|---|
| `test_concurrent_same_cycle_serializable_resolves_one_durable_occurrence_postgres` | `tests/integration/test_clearing_commit_replay_postgres.py:58` | Postgres SERIALIZABLE | both return 30.00; 1 CLEARING tx; 1 audit; exact debts (`:222-255`) | journal not asserted |
| `test_serializable_conflict_without_committed_occurrence_stays_failure_postgres` | same `:323` | Postgres SERIALIZABLE | real 40001, no occurrence → E010; nothing written (`:496-529`) | no journal |
| `test_post_commit_boundary_reconciles_and_new_cycle_still_executes_postgres` (cancellation, ack_loss, connection_loss, connection_loss_reconcile_cancellation) | same `:588-598` | Postgres SERIALIZABLE | after real commit, ack lost / `bind.invalidate()` (`:747`); replay returns 30.00; new cycle clears 5.00; 2 tx, 2 audit, exact debts (`:784-841`) | same process; no journal |
| resolver structural tests | `tests/unit/test_clearing_scope_reaches_every_replay_path.py:57`, `:76` | unit | structural | not behavioural |

## 3. Reliability

| Area | Test | path:line | Backend | Proves | Gaps |
|---|---|---|---|---|---|
| Payment commit outcome unknown (`service.py:1103-1133`, `:1206-1257`) | timeout test above | — | SQLite | timeout branch only | DB-error branch with real commit not tested |
| Connection loss after durable commit | `test_connection_loss_after_commit_is_not_reported_as_a_failed_mutation` | `tests/integration/test_p1_commit_then_refresh_postgres.py:150` | Postgres, own engine | `pg_terminate_backend` after commit; success; durable | trust lines, not payments |
| Failed rollback / recovery read | `test_p1_failed_rollback_state_postgres.py:74`, `:127`; `test_p1_reconcile_after_failed_rollback_postgres.py:98` | Postgres | session state | not payment path |
| Recovery loop | `tests/unit/test_recovery_cleanup.py:64`, `:126` (+ `:210`, `:278`, `:371`, `:407`, `:478`) | SQLite | PREPARED → ABORTED, locks deleted | no Postgres; no replay after abort |
| 40001/40P01 retry | `tests/integration/test_payment_engine_uow_retry_postgres.py:16` (injected), `:201` (real; flows once `:403-409`) | Postgres | whole-unit retry, effects once | 40P01 only in classifier unit tests; no real deadlock |
| Journal under real 40001 | `test_c8_a_real_40001_leaves_one_envelope_and_only_the_successful_attempts_entries` | `tests/integration/test_p015_b4_entries_and_money_postgres.py:598` | Postgres SERIALIZABLE | one envelope | retry loop written in the test |
| Simulator money replay | `tests/integration/test_p015_p1_money_replay_postgres.py:501`, `:515`, `:594`, `:666`, `:714`, `:759`; SQLite `test_p015_p1_money_replay_sqlite.py:380`, `:468`, `:533`, `:567`; unit `tests/unit/test_p015_p1_money_phase_replay.py:503`, `:541`, `:606` | Postgres SERIALIZABLE / SQLite / fakes | replay once; tail failure never replays money | no journal count |
| Stop/hold racing replay | `test_a_tick_that_waited_behind_the_patch_discards_its_attempt_and_the_replay_refuses` | `tests/integration/test_p015_t1544_operator_stop_races_postgres.py:434` | Postgres SERIALIZABLE | replay refuses after stop | simulator path |
| Commit cancellation / unknown in tick | `tests/unit/test_real_tick_commit_cancellation.py:191`; `tests/unit/test_real_payments_ordered_journal.py:544` | fakes | unknown outcome deferred | fakes |

## 4. Process restart

**None.** No test kills a process that committed a payment and replays in a new process or a second app instance.
Existing subprocess use is unrelated to money (`tests/unit/test_p015_b4_entries_and_money.py:863` models-only SQLite
write; `tests/unit/test_run_full_stack_database_url_redaction.py:1366` PowerShell lock holder killed; alembic helpers).
The only uvicorn-in-test is in-process, lifespan off, websocket redaction (`tests/unit/test_websocket_payment_received_event.py:85`).
Nearest crash-like cases are same-process: clearing `connection_loss` (`test_clearing_commit_replay_postgres.py:598`) and
`pg_terminate_backend` after a trust line commit (`test_p1_commit_then_refresh_postgres.py:126-146`). No `os.kill`,
`.terminate()`, `multiprocessing`, or second FastAPI app instance in `tests/`.

## 5. READ COMMITTED dependence (relevant to T1549)

Explicitly pinned READ COMMITTED: `test_payment_idempotency_postgres.py:19` (asserts at `:123`; under SERIALIZABLE the
loser's duplicate insert likely reports 40001 not unique violation → `DBAPIError` branch `service.py:896-925`, retryable
409 instead of "in progress" — inferred); `test_payment_commit_advisory_locks_postgres.py` helper `_use_read_committed`
`:34` used at `:323`, `:1006`, `:1122`, `:1248`; `test_concurrent_prepare_routes_bottleneck_postgres.py:16` (`:119-124`);
`test_concurrent_clearing_payment_lost_update_postgres.py:49` (`:183-188`); `test_clearing_skip_releases_locks_postgres.py:231`
(`:374-379`); `test_p015_step5b_criterion_b_postgres.py:339` deliberate READ COMMITTED control relying on the shared
engine default.

Implicitly READ COMMITTED: every Postgres test using `db_session` or plain `TestingSessionLocal()`;
`test_p015_step5a_reconciliation_postgres.py:345` (docstring `:350`); `app/core/ledger/reconciliation.py:936-952`.

Already on their own SERIALIZABLE engine: clearing replay, P1 money replay, B4/B4a, 5b/5c, T1544 races, inject tests,
`test_p1_reconcile_after_failed_rollback_postgres.py`, `test_payment_engine_uow_retry_postgres.py:300-303`.

## Candidate matrix cells

| # | path × situation → expected | Status |
|---|---|---|
| 1 | `POST /payments` × same signed tx_id after COMMITTED → 200 COMMITTED; debts, tx rows, journal unchanged | partial (status only / debt only, SQLite) |
| 2 | `POST /payments` × after ABORTED → stored ABORTED, no money | not covered |
| 3 | `create_payment` × while NEW/PREPARED → 409 in progress, no second effect | partial (NEW only, READ COMMITTED) |
| 4 | `POST /payments` × different payload → 409, no debt/journal change | partial (status only) |
| 5 | `create_payment` × insert race at SERIALIZABLE → one tx, one effect, one envelope; defined 409 | partial (READ COMMITTED only) |
| 6 | fingerprint-less stored row (T1548) → 409, no money | not covered |
| 7 | commit landed, then timeout or DB error → COMMITTED, abort not called, effects once | partial (fake commit; DB-error branch uncovered) |
| 8 | process restart → COMMITTED; debts and journal unchanged | not covered |
| 9 | `PaymentEngine.commit` × concurrent duplicate → effects once, one COMPLETED envelope | covered (`:702`) |
| 10 | clearing replay after commit with ack/connection lost → same amount, one occurrence | covered except journal |
| 11 | replay after stop / hold → stored result, no money | partial (stop SQLite; hold not covered) |
| 12 | recovery abort of stuck PREPARED, then replay → ABORTED, no money | partial (SQLite, no replay) |
| 13 | engine × real 40001/40P01 → retry, effects once | partial (40P01 classifier only) |
| 14 | simulator money phase × real 40001 → replay once; tail failure never replays | covered; journal not asserted |

Not established from reading: whether unmarked HTTP tests are ever run on Postgres; which of the 136 implicitly
READ COMMITTED Postgres tests change outcome under T1549.
