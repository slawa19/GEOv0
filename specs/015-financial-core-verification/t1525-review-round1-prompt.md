You are the external technical reviewer for GEOv0. Read-only, frozen clone at 4d11a0b with review base 1530878 (branch `review-base` in the clone). Answer in Russian, concisely, with file:line references. REFUTE, do not approve: your job is to find where this batch is wrong, unsafe, or produces false green.

## What this batch is
Task `T1525` of programme 015 (see `specs/015-financial-core-verification/spec.md`, the `T1525` record and the `B4` design records). It changes when a database transaction BEGINS on SQLite, across the whole application and the whole default test tier, and it touches the payment engine's retry classifier.

**The defect it fixes, measured before the fix:** SQLAlchemy 2.0 leaves SQLite transaction control to pysqlite/aiosqlite, which in legacy mode emits `BEGIN` only in front of INSERT/UPDATE/DELETE. A unit of work that has only read has no transaction, so a `SAVEPOINT` opened then is its own transaction and its `RELEASE` commits it; the root `rollback()` that follows undoes nothing. Reproduced through application code: `PaymentEngine.commit` with an invariant violation left the payment `ABORTED` and its debt `7.00000000` stored; a full simulator tick that failed after its payments phase, resolved its observations as rolled back and published no `tx.updated`, kept two `COMMITTED` payments and `925.31` of debt. Over the default tier, 113 savepoints in 63 tests ran with no transaction open, 103 of them committed by their own RELEASE. PostgreSQL was never affected.

**The fix:** `app/db/sqlite_transaction_control.py` — on `connect`, `isolation_level = None` on the adapted DBAPI connection; on SQLAlchemy's `begin`, a deferred `BEGIN`. Installed on every SQLite engine (application, test engine, scratch engines in tests). The WAL / busy_timeout / foreign_keys pragmas moved out of a transaction into the connect listener (inside a transaction `journal_mode` is refused and `foreign_keys` is a silent no-op — both measured).

**Its cost, and the second half of the batch:** a reading transaction now holds a real snapshot, so a write on a stale snapshot fails with `SQLITE_BUSY_SNAPSHOT`, which `busy_timeout` cannot cure. That removed a retry the code used to get for free (the ORM used to raise `StaleDataError`, which `_apply_flow` retried). So the retry classifiers became dialect-aware: `sqlite_busy_error_name` matches the SQLITE_BUSY family by `sqlite_errorcode` (`code & 0xFF == 5`), walking the exception chain, with no message fallback. Changed decision sites: `app/core/payments/engine.py` `_is_retryable_db_error`, the savepoint-mode branch of `_run_uow_with_retry` (busy propagates to the outer owner, the SQLite twin of the existing 40001/40P01 branch), `app/core/payments/service.py` `_classify_payment_db_error`, and `app/core/simulator/real_runner_impl.py` `_is_transient_inject_db_error`. Clearing's `_is_retryable_concurrency_error` was deliberately left PostgreSQL-only and the limitation is commented.

Files changed:

```
 app/core/clearing/service.py                       |  11 +
 app/core/payments/engine.py                        |  33 +-
 app/core/payments/service.py                       |   8 +
 app/core/simulator/real_runner_impl.py             |  13 +-
 app/db/session.py                                  |   7 +
 app/db/sqlite_transaction_control.py               | 141 ++++
 specs/015-financial-core-verification/spec.md      | 130 +++-
 .../t1525-measurements.md                          | 568 ++++++++++++++++
 tests/conftest.py                                  |  46 +-
 ...test_audit_drift_delta_check_sse_integration.py |   3 +
 .../test_p015_t1525_control_postgres.py            | 122 ++++
 ...est_post_tick_audit_drift_runner_integration.py |   3 +
 ...simulator_adaptive_clearing_effectiveness_ab.py |   3 +
 ...test_simulator_adaptive_clearing_integration.py |   3 +
 .../test_simulator_clearing_no_deadlock.py         |   3 +
 .../test_simulator_real_snapshot_db_enrichment.py  | 131 +++-
 tests/unit/test_debt_optimistic_lock.py            |  56 +-
 ...umeric_scale_rounding_is_invisible_on_sqlite.py |   4 +
 .../unit/test_p015_inject_transaction_ownership.py | 101 ++-
 ..._every_sqlite_engine_has_transaction_control.py | 243 +++++++
 ..._t1525_sqlite_savepoint_is_not_a_transaction.py | 723 +++++++++++++++++++++
 ..._p015_t1525_sqlite_stale_snapshot_is_retried.py | 310 +++++++++
 ...1525_sqlite_transaction_control_is_in_effect.py | 126 ++++
 tests/unit/test_sqlite_dev_schema_repair.py        |   5 +
 24 files changed, 2722 insertions(+), 71 deletions(-)
```


## What I need you to attack
1. **Is the fix complete?** Enumerate every place that builds a SQLite engine or connection in `app/`, `tests/`, `scripts/`, `migrations/` and say which lack the control and whether that matters. The batch adds a source guard test — can it be satisfied by a construction it does not see (indirection, a factory, `create_engine` through a helper, a URL built at runtime, an engine created inside a fixture)?
2. **Is the new transaction semantics actually in force where money is written?** Check the payment commit path, the simulator tick's staged payments, clearing and inject: is there any remaining path where a savepoint can still open a transaction, or where a write's transaction begins with a read that could go stale, WITHOUT a retry that restarts the whole unit of work?
3. **The classifier.** Is matching `code & 0xFF == 5` the right predicate (does it catch exactly the "nothing was written, run it again" family and nothing else — consider SQLITE_BUSY vs SQLITE_LOCKED 6 vs SQLITE_PROTOCOL, and extended codes)? Does aiosqlite really preserve `sqlite_errorcode` on every path, including errors raised at `commit()` rather than at a statement? Is there any error now retried that must NOT be (a genuine constraint failure, a disk error, a deadlock that will never clear)? Is the retry budget still finite on every path you find?
4. **Retry correctness.** For each changed decision site: does the retry restart the transaction and re-read, or can it retry inside the same stale snapshot (which would loop until the budget is spent and then fail)? Does the savepoint-mode propagation reach an owner that actually restarts, in the simulator tick and in the HTTP payment path? For inject specifically: after the busy is classified transient, is the "at most once" property of step 3 preserved — can an inject now be applied twice, or dropped?
5. **The pragmas moved to connect.** Verify WAL and foreign_keys are really in effect for every engine, including engines created before the listener is registered and connections already in the pool. Is there a path where a connection is used without the connect listener having run?
6. **Evidence quality.** The batch claims: independent savepoints 113 → 0 over the full tier; a residual rare race in one test (1 in 4 tier runs) removed by changing the test's write to its own transaction; a mutation that did not reproduce at n=4 and was reported as uninformative rather than red. Are those claims the right ones to have made? Which assertion in the new tests could be satisfied by a wrong implementation? Is any new test green for a reason other than the property it names — in particular the source guard, the runtime "control is in effect" checks, and the `isolation_level=None` shape assertion (the batch states outright that removing that line changes no observable behaviour on CPython 3.11 and that the assertion is shape, not effect — is that honest and is the line justified)?
7. **Regressions the batch may not have found.** Reads now hold snapshots: look for application code that reads, waits or polls, and then writes in the same transaction (the batch says it searched and found none — verify), long-lived read transactions that would hold WAL checkpoints, and any place relying on the old "reads are autocommitted" behaviour.
8. **Perimeter.** `T1525` was scoped to SQLite transaction control but reached into `app/core/payments/engine.py`, `service.py` and `real_runner_impl.py`. Is each of those changes necessary to close behaviour this batch broke, or is any of them unrelated scope creep?

Output: a verdict line `T1525: GO | GO-WITH-CHANGES | NO-GO`, then numbered answers, then "Обязательные изменения" (each concrete and testable), then "Ложные утверждения" (any claim above that the code does not support). No code patches.
