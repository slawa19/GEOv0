# 017 stage 3, slice S4 — SQLite and dialect branches outside the money path

- **Date:** 2026-09-24
- **Base:** `2795970` (branch `claude/017-stage3-s3del`, slice S3 on top of `main` `090102e`)
- **Part of:** `T1703` in [`spec.md`](spec.md). Slices S5 (money path), S6 (config, session, launchers,
  runner, README onboarding) and S7 (`app/db/sqlite_transaction_control.py`, `aiosqlite`, the `T1707`
  guard) own the rest of stage 3; their hits are listed below by owner and are **not** touched here.
- **Status authority:** this is a working inventory of one slice; what closes `T1703` is the spec's
  success criteria and the recorded gate evidence, not this file.

## How the inventory was taken

```powershell
git grep -n -i -E "sqlite|dialect\.name|dialect_name|get_dialect|database is locked" 2795970 -- app scripts migrations
```

399 hits on the base. Supplementary scans for forms the regex does not see (all on the base):
`get_backend_name|\.dialect\b|"postgres(ql)?"` (dialect access by other spellings),
`no such table|constraint failed|is locked|in str\(e\)` (SQLite error-text matching) and
`pragma|sqlite_master|busy_timeout`. They added two S4 items: the message-only text fallback of
`app/core/trustlines/service.py::_is_live_trustline_uniqueness_violation` (no "sqlite" word in it) and
the `"no such table"` match in `scripts/cleanup_simulator_runs.py`.

## Classification (hits on the base)

| Class | Hits | Meaning |
|---|---:|---|
| S4 — changed | 90 | executable SQLite branch or dangling reference removed in this slice |
| S4 — file deleted | 35 | SQLite-only script removed with its file |
| prose kept | 23 | comments/docstrings/refusal messages; no executable SQLite path (see below) |
| applied migrations | 66 | `migrations/versions/**`: applied revisions are protected (AGENTS §3, §8) — untouched |
| S5 | 120 | money path — untouched, listed for the orchestrator |
| S6 | 18 | config/session/launchers/runner — untouched, listed |
| S7 | 47 | `app/db/sqlite_transaction_control.py` — untouched, listed |
| **total** | **399** | |

## S4 — what was removed, and why each removal keeps PostgreSQL byte-identical

| Where (base `path:line`) | What | PostgreSQL behaviour |
|---|---|---|
| `app/main.py:323-367` `_sqlite_ensure_debts_version_column` | SQLite-only schema repair; returned on any other backend | never ran on PostgreSQL |
| `app/main.py:369-415` `_sqlite_ensure_equivalents_integrity_hold_column` | same | never ran |
| `app/main.py:418-500` `_SQLITE_INTENT_VERSION_CHECK`, `_sqlite_refuse_pre_027_debt_operations` | same | never ran |
| `app/main.py:508-510` | the three calls in `lifespan` | calls of no-ops removed |
| `app/main.py:9,19` | `import re`, `make_url` — used only by the probes (Ruff F401 after removal) | — |
| `app/core/simulator/storage.py:14` | `sqlalchemy.dialects.sqlite.insert` import | — |
| `app/core/simulator/storage.py:30-50` `_retry_on_locked` + 4 callers (`:117,247,515,725`) | retried only on `OperationalError` whose text contains `"database is locked"` (a SQLite message); anything else re-raised on the first attempt | a single attempt that propagates every exception — exactly what `await _do()` does |
| `app/core/simulator/storage.py:211-230, 360-379` | dialect dispatch `sqlite_insert` / `pg_insert` / `RuntimeError` | the PostgreSQL branch (`insert_fn = pg_insert`) kept verbatim; the bind/dialect read that selected it removed, so `app/` keeps no dialect read here |
| `app/core/simulator/storage.py:256-297, 471-484, 343-351` | `MONEY_METRIC_KEYS`, `_SQLITE_MONEY_WARNED_*`, `_warn_once_sqlite_money_precision`, its call under `if dialect_name == "sqlite"`, the docstring paragraph describing it | only ever fired on SQLite |
| `app/core/simulator/money_replay.py:66,118-122` | `sqlite_busy_error_name` import and branch in `money_conflict_name` | `sqlite_busy_error_name` answers non-`None` only for an exception carrying an integer `sqlite_errorcode` (`app/db/sqlite_transaction_control.py:171-176`); no asyncpg error does, so on PostgreSQL the branch returned `None` and fell to the same `return None` |
| `app/core/simulator/real_runner_impl.py:54,64-65,72-87` | same import and branch in `_is_transient_inject_db_error`, its comment | same argument: the branch was `False` on PostgreSQL and control reached the sqlstate test unchanged |
| `scripts/seed_recipe.py:96,395-405` | same import and branch in `_is_transient` | same argument; now `return False` where it returned `sqlite_busy_error_name(exc) is not None` (always `False` on PostgreSQL) |
| `scripts/seed_recipe.py:257-271` | SQLite arm of `assert_target_is_disposable` | a SQLite URL now reaches the existing final refusal ("backend 'sqlite' is not a database this seed knows how to dispose of") |
| `scripts/validate_test_database_url.py:54-80,94-96,116-130` | `_is_explicit_local_test_database`, the SQLite arm, `_TASK_SLUG_RE`, `PurePosixPath`, docstring | PostgreSQL arm untouched; a SQLite URL now reaches the existing `Unsupported test database backend: sqlite.` refusal |
| `migrations/env.py:79` | refusal message pointed at the deleted `scripts/init_sqlite_db.py` | message is now `Alembic migrations support PostgreSQL only.`; the check itself (`:75-76`) unchanged |
| `app/db/models/trustline.py:48` | `sqlite_where=` dialect kwarg on the live-trustline index | emits nothing on PostgreSQL; `postgresql_where` unchanged |
| `app/db/models/__init__.py:6` | comment naming the deleted `scripts/init_sqlite_db.py` | prose |
| `app/core/trustlines/service.py:92-101` (outside the regex) | text fallback of the live-trustline classifier, reached only by a driver error with neither `constraint_name` nor a sqlstate — SQLite's shape | an asyncpg error always carries a sqlstate; a CHECK/FK violation carries `constraint_name` and returns earlier; 23505 is decided by the chain walk; other sqlstates (e.g. 23502 NOT NULL: "violates not-null constraint", no "unique") returned `False` through the fallback and return `False` now |
| `scripts/cleanup_simulator_runs.py:215-219` (outside the regex) | `"no such table"` match — SQLite's message; PostgreSQL says `relation ... does not exist` | the `else` arm (the `[WARN]` line) kept verbatim; it was the only arm PostgreSQL reached |
| `scripts/seed_db.py:206,277` | docstrings "Seed SQLite DB" | prose |

### Deleted files (with every caller)

| File | Callers found (`git grep`, whole repository) | Disposition |
|---|---|---|
| `scripts/init_sqlite_db.py` | `migrations/env.py:79` (message, changed), `tests/unit/test_alembic_postgres_only.py:14` (changed), `app/db/models/__init__.py:6` (comment, changed); docs: `admin-ui/docs/real-api-integration.md:163`, `docs/ru/06-contributing.md:241`; historical specs 006, 017, 022 | docs not edited (see "left for others") |
| `scripts/check_sqlite_db.py` | docs: `.github/copilot-instructions.md:41,172`, `admin-ui/docs/real-api-integration.md:178`, `docs/ru/testing/quick-start-and-debugging.md:59`; historical specs 001, 009, 015 | same |
| `scripts/check_real_snapshot_debts_sqlite.py` | historical specs 001, 015 only | — |
| `scripts/t1525_savepoint_detector.py` | its own usage text; `specs/015-.../t1525-measurements.md` (historical measurement record) | measures SQLite only (spec owner surface: "удаление: измеряет поведение SQLite") |

No `package.json`, `docker/`, `.ps1` or `.github/workflows/` caller of any of the four exists.

## Tests changed, and why (AGENTS §11)

| Test | Change | Justification |
|---|---|---|
| `tests/unit/test_background_task_supervision.py:178-180` | three `monkeypatch.setattr(main_module, "_sqlite_*", AsyncMock())` lines removed | they stubbed functions that no longer exist; the test's assertions (shutdown order) are untouched |
| `tests/unit/test_alembic_postgres_only.py:12-15` | `_MESSAGE` follows the new refusal text | the refusal is still asserted, with `returncode != 0`, and no revision ran |
| `tests/unit/test_trustline_conflict_identity.py` | SQLite-message `True` case moved to the `False` list; `test_text_fallback_requires_the_full_triple` deleted; new `test_postgres_unique_violation_on_two_of_the_three_columns_is_not_ours` | the deleted test pinned the removed fallback (its own docstring: "The SQLite fallback"). Its load-bearing assertion — two of the three columns are not ours — is carried over in the PostgreSQL form (23505 on `trust_lines` whose DETAIL names a pair). The positive counter-check stays in the existing 23505 tests |
| `tests/unit/test_test_database_guard.py` | five SQLite tests (two acceptance, three path-rule refusals) and `test_rejects_safe_sqlite_when_postgresql_backend_is_required` replaced by `test_rejects_every_sqlite_database` (4 URLs × with/without `required_backend`) | they pinned the removed SQLite acceptance and its path rules. The replacement includes the three URL shapes that were ACCEPTED, so it would fail if acceptance came back. The PostgreSQL cases are untouched |
| `tests/unit/test_p017_t1711_seed_recipe_refuses.py` | `sqlite :memory:` moved from the accepted to the refused list, a `.local-run/` SQLite file added to the refused list; `test_a_sqlite_file_is_accepted_under_local_run_and_refused_outside_it` deleted | the deleted test pinned the removed SQLite arm; the two URLs it and the accepted list admitted are now asserted refused. The PostgreSQL accepted/refused pairs are untouched |
| `tests/unit/test_the_tier_refuses_a_database_that_is_not_postgres.py` | docstring only: the URL guard no longer "still accepts" SQLite | prose made false by this slice |

Not changed on purpose: the allow-list in `tests/unit/test_p014_t1406_no_mutable_database_in_the_working_tree.py`
still names `test_p017_t1711_seed_recipe_refuses.py`, and its rationale still holds (the file quotes
SQLite URLs as DATA that the guard refuses).

## Prose kept (23), and why

Comments and docstrings with no executable SQLite path: `app/api/v1/admin.py:1332,1431,1551`,
`app/core/simulator/inject_executor.py:44`, `app/core/simulator/money_replay.py:11,92` (the latter
describes what `app/core/payments/service.py` classifies — S5's surface; it goes stale when S5 lands),
`app/core/simulator/post_tick_audit.py:138` (the chunking it explains still runs on PostgreSQL),
`app/core/simulator/real_runner_impl.py:695`, `app/db/models/debt.py:32,51`,
`app/db/models/equivalent.py:33`, `app/db/models/simulator_storage.py:14,68,96,123`,
`app/main.py:581` (the `engine.dispose()` it explains stays), `scripts/cleanup_simulator_runs.py:88`,
and refusal messages that already name SQLite as refused: `scripts/dev_database.py:146`,
`scripts/measure_clearing_min_amount_plan.py:50,103`, `scripts/run_simulator_run_and_analyze.py:103`.

`app/schemas/equivalents.py:28` and `app/schemas/trustline.py:28` are comments on validators that
attach UTC to a naive timestamp. The validators are value-based, not dialect branches, and apply to any
naive `datetime` a caller hands them, so removing them is not a byte-identical change; left in place.

Two dialect reads remain in `app/` outside S5/S6/S7 and are **not branches**:
`app/api/v1/health.py:132,143` report `get_backend_name()` in the `/health/db` body (a wire field —
OpenAPI is out of scope). The `T1707` guard will need them allow-listed or decided.

## Left for others (untouched here)

**S5 — money path (120 hits):**

- `app/core/clearing/service.py` (30): 97, 271, 274, 309, 310, 328, 395, 397, 401, 402, 406, 407, 412, 438, 465, 466, 491, 656, 659, 660, 663, 664, 737, 787, 790, 943, 1006, 1451, 1523, 1840
- `app/core/ledger/journal.py` (22): 108, 177, 485, 511, 540, 663, 1242, 1243, 1335, 1573, 2031, 2072, 2087, 2140, 2266, 2730, 2731, 2734, 2736, 2745, 2791, 2977
- `app/core/ledger/reconciliation.py` (18): 14, 15, 44, 107, 940, 942, 944, 949, 954, 955, 957, 982, 1064, 1069, 1072, 1073, 1075, 1163
- `app/core/payments/engine.py` (19): 35, 137, 142, 144, 145, 467, 468, 606, 607, 611, 621, 622, 784, 785, 792, 793, 819, 846, 852
- `app/core/payments/service.py` (14): 19, 57, 66, 68, 74, 117, 120, 131, 1447, 1449, 1451, 1456, 1474, 1481 (1447-1481 is a `dialect_name == "sqlite"` branch)
- `app/db/journal_tables.py` (8): 15, 20, 194, 201 (`sqlite_where`), 273, 275, 278, 279 — ambiguous S4/S5 (journal table definition), left to S5
- `app/db/reconciliation_tables.py` (1): 170 (`sqlite_where`) — ambiguous S4/S5, left to S5
- `app/db/types.py` (8): 19, 25, 26, 70, 73, 79, 102, 151

**S6 (18 hits):** `app/config.py:54,55,57,59`; `app/db/session.py:9,12,15,29,30,31,35,42,43,48,61`;
`scripts/run_full_stack.ps1:720`; `scripts/run_local.ps1:544`; `scripts/verify_local.ps1:116`.
Also S6's by consequence: `scripts/cleanup_simulator_runs.py`, `scripts/dev_database.py:499`,
`scripts/seed_db.py:721` and `scripts/take_reconciliation_baseline.py` open the database through
`app.db.session.AsyncSessionLocal` and so accept whatever `DATABASE_URL` `app/config.py` accepts;
after this slice the app itself also starts on a SQLite URL without any probe until `T1704` makes the
URL PostgreSQL-only.

**S7 (47 hits):** `app/db/sqlite_transaction_control.py` (whole file). After this slice its remaining
importers are S5's (`app/core/payments/engine.py:35`, `app/core/payments/service.py:19`,
`app/core/ledger/journal.py:177`, `app/core/ledger/reconciliation.py:107`) and S6's
(`app/db/session.py:9`); `scripts/seed_recipe.py` and the two simulator modules no longer import it.

**Docs naming deleted scripts or the removed warning** (not in S4's surface; for the doc owner):
`.github/copilot-instructions.md:41,172`, `admin-ui/docs/real-api-integration.md:163,178`,
`docs/ru/06-contributing.md:241`, `docs/ru/testing/quick-start-and-debugging.md:59`,
`docs/ru/simulator/backend/run-storage.md:166` (`simulator.storage.sqlite_money_metrics_are_not_exact`).
`migrations/versions/028_equivalent_integrity_hold.py:18` names the removed `_sqlite_ensure_…` probe —
applied revision, left as history.
