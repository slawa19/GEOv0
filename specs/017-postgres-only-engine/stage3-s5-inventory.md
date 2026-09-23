# 017, stage 3, slice S5 — SQLite leaves the money path (`T1703`, money part)

- **Date:** 2026-09-24
- **Programme:** [017 — PostgreSQL as the only engine](spec.md), task `T1703`
- **Base:** `claude/017-stage3-s3del` at `2795970` (S3, PR #31). Branch `claude/017-stage3-s5`.
- **Status authority:** descriptive. What this slice did is established by the commit, the mutations
  and the gate lines of its hand-off report, not by this file.
- **Scope:** dialect branches of the money path — `app/core/payments/`, `app/core/clearing/`,
  `app/core/ledger/`, the money-conflict / retry predicates of the simulator
  (`money_replay.py`, `real_runner_impl.py`) and the owner-lock call of `real_tick_orchestrator.py`,
  `app/db/types.py`. **Not touched:** `app/db/session.py`, `app/db/sqlite_transaction_control.py`,
  `app/config.py` (S6/S7), `app/main.py`, `app/core/simulator/storage.py`, scripts, migrations (S4).
- **Rule applied:** the non-PostgreSQL arm is deleted, the PostgreSQL arm is kept verbatim. No new
  entity, no change to money semantics, OpenAPI, migrations or SSE.

## How the inventory was taken

`git grep -n -i -E "sqlite|dialect\.name|dialect_name|get_dialect|database is locked|busy" -- app/core app/db/types.py`
on the base: **171 hits in 11 files**. That pattern does not see a branch spelled through a helper
(`if not self._is_postgres():`, `self._dialect_name() not in {...}`), so a second pass grepped
`_is_postgres|_is_sqlite|_dialect_name|"postgresql"` over `app/`, which added the seven engine lock
guards and the retry-predicate guard, `payments/service.py:531` and `real_tick_orchestrator.py:298`.
Every hit was read at the hit.

| file | hits | class |
|---|---|---|
| `app/core/simulator/storage.py` | 35 | **other slice (S4)** — dialect upsert, `"database is locked"` text retry; not touched |
| `app/core/clearing/service.py` | 32 | 7 branch points **S5**, rest prose |
| `app/core/ledger/journal.py` | 22 | 1 branch point + import **S5**, rest prose / dialect-generic code |
| `app/core/payments/engine.py` | 21 (+8 via `_is_postgres`) | 11 branch points **S5**, 2 prose |
| `app/core/ledger/reconciliation.py` | 18 | 2 branch points + import **S5**, rest prose |
| `app/core/payments/service.py` | 15 (+1 via `_is_postgres`) | 5 branch points **S5**, rest prose |
| `app/core/simulator/real_runner_impl.py` | 10 | 1 predicate arm + import **S5**, rest prose |
| `app/core/simulator/money_replay.py` | 8 | 1 predicate arm + import **S5**, rest prose |
| `app/db/types.py` | 8 | **prose only** — no dialect branch exists (see below) |
| `app/core/simulator/inject_executor.py` | 1 | prose |
| `app/core/simulator/post_tick_audit.py` | 1 | prose (chunking comment, not money) |
| `app/core/simulator/real_tick_orchestrator.py` | 0 (+1 via `_is_postgres`) | 1 branch point **S5** |

## 1. Deleted branches (line numbers of the base `2795970`)

### `app/core/payments/engine.py`

| before | what | on PostgreSQL before → after |
|---|---|---|
| `:35` | `from app.db.sqlite_transaction_control import sqlite_busy_error_name` | — |
| `:137-145` | `_dialect_name`, `_is_postgres`, `_is_sqlite` | helpers; dead after the guards below went |
| `:182-183` | `_acquire_equivalent_owner_locks`: `if not self._is_postgres(): return` | guard was False → lock taken; now taken unconditionally |
| `:206-207` | `acquire_staged_equivalent_owner_locks`: same guard | same |
| `:237-238` | `acquire_session_equivalent_owner_lock`: same guard | same |
| `:254-255` | `release_session_equivalent_owner_lock`: `return True` guard | same (real `pg_advisory_unlock` result) |
| `:269-270` | `_acquire_tx_advisory_lock`: same guard | same |
| `:308-309` | `_acquire_segment_advisory_lock_keys`: same guard | same |
| `:584-585` | `_preacquire_equivalent_owner_locks_for_tx`: `return None` guard | same; the callers' `is not None` checks stay (they also cover the reuse-outer-lock path) |
| `:605-622` | `_is_retryable_db_error`: non-PG arm returning `_is_sqlite() and sqlite_busy_error_name(exc)` | PG arm verbatim: `40P01`/`40001` retryable, the two exact `23505` identity races retryable, everything else False |
| `:784-786` | `_run_uow_with_retry`: `sqlite_busy = sqlite_busy_error_name(exc) if self._is_sqlite() else None` | was always None on PG |
| `:792-796` | savepoint re-raise of a SQLite busy (the "SQLite twin" of the `40P01`/`40001` re-raise) | the PG re-raise at `:787` is kept verbatim |
| `:845-852` | `sqlite_error=%s` field of `event=payment.uow_retry` | **log line changes**: the field printed `sqlite_error=None` on PG and is gone; the three log assertions in tests match the prefix `event=payment.uow_retry op=commit` and are unaffected |

### `app/core/payments/service.py`

| before | what | on PostgreSQL |
|---|---|---|
| `:9` | `func` in `from sqlalchemy import …` | unused once `func.datetime` went (Ruff F401) |
| `:19` | import of `sqlite_busy_error_name` | — |
| `:117-132` | `_classify_payment_db_error`: SQLite-busy arm | PG arm verbatim: `40001`/`40P01` through `orig`/`__cause__` → `RetryablePaymentConflictException`, else `GeoException` |
| `:531` | `acquire_staged_equivalent_owner_locks`: `or not self.engine._is_postgres()` | was False on PG; `if not codes: return` stays |
| `:1441-1452` | `list_payments`: bind / dialect lookup | only fed the branches below |
| `:1456-1459` | naive-UTC normalisation for SQLite | PG normalisation to aware UTC verbatim |
| `:1474-1477`, `:1481-1484` | `func.datetime(...)` comparisons for SQLite | PG `created_at >= / <= date` verbatim |
| `:57-58` | docstring sentence naming "the SQLite busy check" | reworded to name `_classify_payment_db_error` |

### `app/core/clearing/service.py`

| before | what | on PostgreSQL |
|---|---|---|
| `:395-402` | `_dialect_name` (swallowed every exception → `None`), `_is_sqlite` | dead after the branches below |
| `:406-408` | `_bind_uuid`: SQLite `uid.hex` | `return uid` verbatim; the function stays (three callers), now the identity |
| `:438` | comment "SQLite stores UUIDs as bare hex" | shortened |
| `:465-474` | `_sql_auto_clearing_ok`: `json_extract` predicate | PG `policy->>'auto_clearing'` predicate verbatim |
| `:656`, `:659-664` | triangles: `dialect = …`, `min(...)` for SQLite, SQLite bind comment | `LEAST(...)` verbatim |
| `:787`, `:790-791` | quadrangles: same | `LEAST(...)` verbatim |
| `:1523` | `execute_clearing_with_amount`: `self._dialect_name() not in {"postgresql","postgres"} or not cycle` | `if not cycle:` — the dialect half was False on PG |

### `app/core/ledger/journal.py`

| before | what |
|---|---|
| `:177` | import of `sqlite_transaction_control_is_installed` |
| `:2730-2748` | `_refuse_unusable_transaction`: the `if conn.engine.dialect.name == "sqlite":` block (two `NO_TRANSACTION_CONTROL` refusals). Never entered on PG. `Reason.NO_TRANSACTION_CONTROL` (`:242`) and `_driver_transaction_is_live` (`:1285`) are **kept**: the enum value is part of the refusal vocabulary and the probe is read by `tests/integration/test_p015_t1528_the_statement_is_read_not_guessed_postgres.py:286` |

### `app/core/ledger/reconciliation.py`

| before | what |
|---|---|
| `:107` | import of `sqlite_transaction_control_is_installed` |
| `:954-960` | `open_verification_snapshot`: `elif dialect == "sqlite"` arm. The `postgresql` arm and the fail-closed `else: raise ReconciliationSnapshotError` are kept verbatim |
| `:1072-1078` | `_open_reaction_transaction`: same |

### Simulator money predicates

| before | what | on PostgreSQL |
|---|---|---|
| `money_replay.py:66` | import | — |
| `money_replay.py:118-122` | `money_conflict_name`: SQLite-busy arm | typed `RetryablePaymentConflictException` → name; `DBAPIError` with `40001`/`40P01` → the SQLSTATE; everything else `None` — verbatim |
| `money_replay.py:92` | docstring pointer to the removed busy arm and a stale `:93-110` range | reworded |
| `real_runner_impl.py:54` | import | — |
| `real_runner_impl.py:72-87` | `_is_transient_inject_db_error`: SQLite-busy arm (was checked **first**) | `40001`/`40P01`/`55P03` → True, else False — verbatim |
| `real_runner_impl.py:64-65` | comment "The SQLite busy family joins them" | shortened |
| `real_tick_orchestrator.py:298` | `if owner_service.engine._is_postgres():` around the owner-lock call | call now unconditional, body verbatim |

### `app/db/types.py`

**No dialect branch exists.** `MoneyNumeric.process_bind_param` refuses non-finite values on every
dialect; the eight hits are prose (a dated SQLite measurement in the module docstring,
`finite_money_clauses` docstring) and one exception message. Nothing deleted. The `MONEY_ROUND_TRIP`
check the brief attributes to this module lives in `app/core/ledger/journal.py::_check_storable`
(`:507-545`) and `_round_trip` (`:481-504`); it is **dialect-generic** (it asks the column type's own
processors, with no dialect test) and is the identity on asyncpg, so on PostgreSQL it cannot fire.
Kept: removing it would delete a guard, not a SQLite branch. Whether it should go is a decision for
the orchestrator, not a mechanical S5 deletion.

## 2. Kept — not dialect branches

- `journal.py:2045-2064` `_PARAMSTYLES` / `_raw_params`: a table over every DBAPI paramstyle with a
  fail-closed refusal for an unknown one; `qmark` is aiosqlite's, but the table is generic by design.
- `journal.py:1238-1282` `_driver_transaction_probe`: its `in_transaction` attribute fallback is
  sqlite3's spelling, unreachable on asyncpg (which answers `is_in_transaction()` first); it is
  duck-typing without a dialect test and is shared by the `begin` guard. Not removed.
- `reconciliation.py:948`, `:1061`: `dialect = bind.dialect.name` feeding the kept
  `postgresql`-or-refuse dispatch.
- `clearing/service.py:_is_retryable_concurrency_error` (`:305-329`): already PostgreSQL-only; its
  comment names `sqlite_busy_error_name` as history. Not edited.

## 3. Prose left in place

All remaining hits in the files above are docstrings/comments: dated SQLite measurements
(`journal.py:108,484,510,662,1241,1334,1572,2030,2071,2086,2139,2265,2771,2957`,
`reconciliation.py:14-15,44,939-943,974,1056,1148`, `types.py:19-26,70-79,102,151`,
`service.py:65-73`, `clearing/service.py:97,271-274,309-328,400,468,707,909,972,1417,1806`,
`engine.py:438-439,760-761`, `money_replay.py:11`, `real_runner_impl.py:677`,
`inject_executor.py:44`, `post_tick_audit.py:138`). They are left for the documentation sweep; some
now describe code that no longer exists (`engine.py:438-439` "SQLite gets the plain refusal",
`clearing/service.py:1806` "the path without an interlock (SQLite, …)").

## 4. Tests changed

No test deleted, none added; the collected set is unchanged.

| test | change | why (AGENTS.md §11) |
|---|---|---|
| `tests/unit/test_payment_engine_retry_savepoint_nocommit.py` (4 tests) | dropped `monkeypatch.setattr(eng, "_is_postgres", lambda: True)` (and its comment) | the attribute is gone; the patch forced the only arm that now exists, so the tests assert exactly what they asserted before |
| `tests/unit/test_p015_t1529_the_envelope_identity_is_a_retryable_race.py::_engine` | same | same |
| `tests/unit/test_p015_inject_transaction_ownership.py::test_a_non_transient_staging_error_is_recorded_not_retried` | docstring: removed "(and, until SQLite leaves `app/`, the SQLite busy family)" | the promised event happened |

**Tests that were riding the non-PostgreSQL arm without saying so** — found by the first full-tier
run on this slice (14 failures and a hang, all in `tests/unit/`). Each uses a fake session with no
bind (`bind = None` or no attribute), so `_is_postgres()` was False and the owner / tx lock calls
were silently skipped. With the guards gone the calls reach the fake. Owner locks are not what these
tests measure (they are measured on PostgreSQL, e.g.
`tests/integration/test_payment_commit_advisory_locks_postgres.py`), so the call is now stubbed
explicitly, exactly as `test_real_payments_ordered_journal.py:203` already did for the test that does
assert on it:

| test | change |
|---|---|
| `tests/unit/test_real_tick_orchestrator_rollback_resolution.py` (8 tests; 7 failed, the 8th hung on its blocking rollback) | `_bind_session` also stubs `PaymentService.acquire_staged_equivalent_owner_locks` |
| `tests/unit/test_real_payments_ordered_journal.py` (5 failed: `RuntimeError: viz queries disabled` from the fake's `execute`) | module-level autouse fixture stubbing the same method; the test that asserts owner-first order still installs its own double |
| `tests/unit/test_real_runner_tick_nested_partial_failures.py::test_real_runner_tick_real_mode_uses_nested_tx_and_survives_one_action_error` | same stub |
| `tests/unit/test_payment_engine_advisory_locks_execute.py::test_commit_acquires_keys_derived_from_loaded_prepare_locks` | the fake now answers the owner preflight's lock read, `_acquire_equivalent_owner_locks` is captured, and the assertion **grows**: owner → tx → segment instead of tx → segment, which is the order PostgreSQL runs |

No test pinned a removed SQLite arm: S3 had already deleted the SQLite stands
(`stage3-s3-inventory.md`, section 1).

## 5. Anti-vacuum — one mutation per edited predicate arm, each reverted

Applied to the working tree, the node run through `scripts/verify_local.ps1 -TaskSlug p017s5
-BackendOnly -BackendSelector <node>`, the file restored byte for byte (sha256 checked). Every run
exit `1`, `1 failed`.

| # | predicate | mutation | red test | failure |
|---|---|---|---|---|
| M1 | `PaymentEngine._is_retryable_db_error` | `{"40P01", "40001"}` → `{"40P01"}` | `tests/integration/test_p017_uow_retry_after_a_real_40001_postgres.py::test_the_same_40001_is_retried_when_the_rollback_succeeds` | the real `asyncpg SerializationError` propagates instead of being retried |
| M2 | `_run_uow_with_retry` savepoint re-raise | `if use_savepoint and pgcode in {...}:` → `if False:` | `tests/unit/test_payment_engine_retry_savepoint_nocommit.py::test_staged_serialization_failure_is_owned_by_outer_transaction` | `DID NOT RAISE DBAPIError` |
| M3 | `_classify_payment_db_error`, accepting half | SQLSTATE test → `if False:` | `tests/integration/test_p015_t1525_classification_reads_deliberate_wrapping_only_postgres.py::test_a_genuine_40001_is_still_retryable_on_both_classifiers` | `isinstance(GeoException(...), RetryablePaymentConflictException)` is False |
| M4 | `_classify_payment_db_error`, refusing half | every `DBAPIError` → retryable | `…_postgres.py::test_a_terminal_error_inside_a_40001_handler_is_not_retryable` | a terminal `23505` classified retryable |
| M5 | `money_conflict_name`, accepting half | SQLSTATE test → `if False:` | `tests/unit/test_p015_p1_money_conflict_predicate.py::test_a_real_serialization_failure_is_a_money_conflict` | `assert None == '40001'` |
| M6 | `money_conflict_name`, refusing half | every `DBAPIError` → `"ANY_DATABASE_ERROR"` | `…::test_a_real_integrity_error_on_the_same_backend_is_not_a_money_conflict` | `assert 'ANY_DATABASE_ERROR' is None` on a real `ForeignKeyViolationError` |
| M7 | `_is_transient_inject_db_error`, accepting half | `return sqlstate in …` → `return False` | `tests/integration/test_p015_inject_retries_a_serialization_failure_postgres.py::test_a_real_serialization_failure_restarts_the_whole_inject_unit_of_work` | `staged 1x`, `assert 1 == 2` |
| M8 | `_is_transient_inject_db_error`, refusing half | → `return True` | `tests/unit/test_p015_inject_transaction_ownership.py::test_a_non_transient_staging_error_is_recorded_not_retried` | `assert 2 == 1` (a `23505` was retried) |
| M9 | clearing interlock gate (`:1523` before) | `if not cycle:` → `if True:` | `tests/integration/test_p017_t1702_mode_b_fixture_postgres.py::test_mode_a_session_is_refused_by_clearing` | `DID NOT RAISE GeoException` |

## 6. Adversarial pass

- **Did a removed arm also catch a PostgreSQL failure?** No. Every removed arm matched through
  `sqlite_busy_error_name`, which reads only an integer `sqlite_errorcode` attribute
  (`app/db/sqlite_transaction_control.py:171`); asyncpg's exceptions and SQLAlchemy's wrappers carry
  none. No arm in this surface matched `"database is locked"` text — that matcher is in
  `app/core/simulator/storage.py:40` (S4).
- **Fail-open removed, not widened (a finding, not a regression on a real session).** The deleted
  guards decided by the session's bind: the engine's `_is_postgres()` read `session.bind` and
  clearing's `_dialect_name()` swallowed any exception into `None`. A PostgreSQL session *without* a
  bind would therefore have skipped every advisory lock and the clearing interlock silently. Every
  session the application builds has one (`app/db/session.py:85`, `clearing/service.py:346`,
  `:1648`), so this was not reachable; after this slice such a session gets the lock SQL (engine) or
  `GeoException` (clearing, `bind` not an `AsyncEngine`) instead of an unlocked run.
- **Callers of every edited function** (grep over `app/`, `scripts/`, `tests/`, `admin-fixtures/`):
  `_is_retryable_db_error` — `engine.py` `_run_uow_with_retry` only (plus tests);
  `_classify_payment_db_error` — `payments/service.py` (4 call sites, all `raise … from exc`
  mappings) and `tests/integration/test_p015_t1523_…` (spy); `money_conflict_name` —
  `money_replay.py:434`, `real_tick_orchestrator.py:666`; `_is_transient_inject_db_error` —
  `real_runner_impl.py` inject loop (2 sites); `PaymentService.acquire_staged_equivalent_owner_locks`
  — `real_tick_orchestrator.py:298`; `_bind_uuid` — `clearing/service.py` (3 sites);
  `_is_postgres` — only the removed guards, `payments/service.py:531`,
  `real_tick_orchestrator.py:298` and six test monkeypatches, all edited.
- **Left for another slice:** `scripts/seed_recipe.py:96,405` imports `sqlite_busy_error_name`
  (S4/S7 must remove it before `sqlite_transaction_control.py` goes);
  `clearing/service.py:328` names it in a comment.
