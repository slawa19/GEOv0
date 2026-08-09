# Phase 6 cleanup evidence map

- **Date:** 2026-08-09
- **Execution base:** `d6e3e094d14422e20075a70839b0b288efe51bce`
- **Branch:** `codex/codebase-renovation-phase6`
- **Scope:** REN-013B, REN-014 and REN-016 only
- **Status:** COMPLETE — accepted product HEAD `90ac5ef6197c184b5818fe52826a8e97dbb5f6d5`

This ledger was written before Phase 6 implementation. It separates observed
behavior from accepted intent and the bounded target. Code, runtime state, tests,
Git history and documentation remain evidence rather than interchangeable sources
of truth.

## Baseline and frozen boundary

`git status --short` was clean at the execution base. `git log --oneline
--decorate -30` and `git show --stat d6e3e094d14422e20075a70839b0b288efe51bce`
confirmed the requested Phase 5 sequence:

- accepted product behavior: `ff53dbc1c070220ad7ddfdaf8a0aa5c8c1ccd157`;
- final test evidence: `345991b47f15e2ed4080c7617f4f3430883f8b7b`;
- closeout and Phase 6 execution base:
  `d6e3e094d14422e20075a70839b0b288efe51bce`.

There is no baseline disagreement to resolve. Phase 7/REN-015, product features,
REST/SSE/OpenAPI/DB schema changes, Simulator v1 implementation, archive deletion,
fixture/schema regeneration and visual snapshot updates are non-goals.

## Owner surfaces

| Slice | Owned writes | Read-only / forbidden by default |
|---|---|---|
| REN-013B | proved test-only deletions in Phase 2–5 touched suites | production code, unrelated suites, visual baselines |
| REN-016 runtime DB | `app/config.py`, SQLite bootstrap/diagnostic callers, local PowerShell entrypoints, focused tests | existing root DBs/logs/PIDs/NDJSON and applied migrations |
| REN-016 test output | `tests/conftest.py`, `pytest.ini`, `scripts/verify_local.ps1`, Playwright configs, focused guards | generated fixtures and snapshots |
| REN-016 classification | Admin starter remnants and current v1/generated-boundary documentation | Simulator v1 code and generated fixture bodies |
| REN-014 | current README/RU front doors and explicit EN/PL status banners | historical bodies except a minimal status/successor banner |

The orchestrator owns cross-slice decisions and the shared specification files.
Three independent read-only audits covered test value, runtime producers and
documentation/reference authority; their claims are accepted only where the
commands and owner paths below were independently reproduced.

## REN-013B — test-value decisions

### Current behavior

- `simulator-ui/v2/src/components/GraphNavigator.test.ts:55-60` dispatches an
  Enter `keydown` without observing it, then calls `.click()`; only the click
  produces the asserted callback and live-status result. Actual keyboard
  activation is covered by
  `simulator-ui/v2/e2e/phase5-functional-smoke.spec.ts:607-617`.
- `simulator-ui/v2/src/components/ActionBar.test.ts:153-163` reads exact Vue/CSS
  source. It is a narrow shared-HudBar ownership guard, while its mounted tests
  cover busy/disabled/live-region behavior. There is not yet a non-vacuous
  narrow-viewport browser replacement for the layout policy.
- `tests/integration/test_concurrent_prepare_routes_bottleneck_postgres.py:286-483`
  overlaps the stronger committed end-to-end bottleneck case at `:15-283`, but a
  fresh disposable PostgreSQL runtime is unavailable locally (`docker` is not
  installed), so deletion cannot receive a post-change semantic gate in Phase 6.
- `tests/unit/test_equivalent_code_validation.py:18-19` is syntactically
  assertion-free but observes the unique positive-boundary contract by completing
  without an exception; it is not pass-only.

### Intended behavior

REN-013B removes only a false signal, a behaviorally duplicated test, or a test for
a removed contract. Explicit policy/architecture guards and unique positive or
failure boundaries remain.

### Optimal bounded target and classification

| Candidate | Decision | Reason / replacement |
|---|---|---|
| GraphNavigator unobserved Enter dispatch | **safe delete** | no-op step; scoped Chromium already proves native keyboard activation |
| ActionBar exact shared-HudBar/CSS assertions | **keep** | explicit narrow owner-policy guard until a non-vacuous layout proof exists |
| duplicate PostgreSQL lower-level bottleneck case | **verify first / keep in Phase 6** | stronger test exists, but the required fresh PostgreSQL execution is unavailable |
| positive Equivalent validation cases | **keep** | unique no-exception boundary values |
| OpenAPI exact comparisons and stale-run/scenario-switch tests | **keep** | explicit contract guards or distinct observable state transitions |

No giant test-file split, source-test sweep, taxonomy rewrite, snapshot update or
coverage target is justified.

## REN-016 — runtime and repository decisions

### Current behavior

- `app/config.py:54` defaults to `sqlite+aiosqlite:///./geov0.db`;
  `app/db/session.py:9-29` consumes it, and `app/main.py:297-338` names the root
  file in compatibility guidance.
- `scripts/run_local.ps1:615,630,724` and
  `scripts/run_full_stack.ps1:452,463,600` independently assume root
  `geov0.db`. Diagnostic helpers do the same at
  `scripts/check_sqlite_db.py:13`,
  `scripts/check_real_snapshot_debts_sqlite.py:45-47` and
  `scripts/run_simulator_run_and_analyze.py:342`.
- Logs and current PID producers are already below `.local-run/` in
  `scripts/run_local.ps1:81,91-94` and
  `scripts/run_full_stack.ps1:88,100-109`.
- `tests/conftest.py:31-38` defaults direct pytest to root
  `.pytest_geov0.db`; `pytest.ini` leaves `.pytest_cache` at the root;
  `tests/integration/test_simulator_super_smoke.py:161-220` falls back to root
  `test-results`. The canonical verifier already owns task-local DB, basetemp and
  failure artifacts at `scripts/verify_local.ps1:81-127`, but not its pytest
  cache.
- `admin-ui/playwright.config.ts:13-23` and
  `simulator-ui/v2/playwright.config.ts:13-26` use Playwright package-local
  defaults for results/reports. Phase 4 and Phase 5 scoped configs already have
  dedicated output paths.
- The existing ignored root `geov0.db` is not a fixture: a read-only SQLite probe
  found 15 tables, 110 participants, 481 transactions and 200 simulator runs.
  It and all other ignored DB/log/PID/NDJSON/report/cache paths are user-owned and
  will not be moved or deleted in this phase.
- Tracked-artifact scan over `git ls-files` found no tracked DB/log/PID/NDJSON/
  report/cache/test-output file. Admin canonical/public fixture trees contain 37
  files each with zero hash differences. Simulator cached fixture copies are
  required for the documented offline fallback.
- `simulator-ui/v1` is absent from normal gates but its own README does not yet
  carry the repository's read-only historical classification.
- `admin-ui/src/components/HelloWorld.vue` and
  `admin-ui/src/assets/vue.svg` have no import/caller; `admin-ui/index.html:5-7`
  still consumes the Vite favicon and generic title.

### Intended behavior

Default mutable DB, log, PID, pytest and Playwright output belongs below the one
ignored `.local-run/` root. Explicit `DATABASE_URL` and output overrides remain
authoritative. Existing root data is never migrated or deleted automatically.
Tracked canonical/generated fixture copies remain reproducible, Simulator v1 is
historical/read-only, and only reference-proven starter remnants are removed.

### Optimal bounded target

1. Move the default dev SQLite path to `.local-run/geov0.db`, ensure its parent is
   created on clean bootstrap, and align canonical local/diagnostic callers.
2. Preserve an explicit `DATABASE_URL`, including the manual legacy override
   `sqlite+aiosqlite:///./geov0.db`; do not silently reset a non-default database.
3. Move direct pytest DB/cache/failure output and normal Playwright result/report
   defaults under `.local-run/`, with task/output overrides retained.
4. Keep ignored historical root artifacts in place and report them for separate
   owner cleanup approval.
5. Mark v1 read-only, verify fixture sync produces no unexplained diff, and remove
   only the proven Admin starter files in an independent commit.
6. Preserve ignored package `.env.local` as user-local frontend configuration;
   it is not classified as a runtime report/artifact and will not be redesigned in
   this cleanup slice.

## REN-014 — documentation decisions

### Current behavior

- `docs/README.md` and `docs/ru/documentation-rules.md` already define OpenAPI as
  REST authority, current RU documents as accepted intent, EN/PL as dated
  translations only, and archives/concepts as non-normative.
- `docs/ru/config-reference.md:3-451` instead claims a nonexistent YAML runtime
  configuration system and invented mutable keys. Actual executable settings are
  `app/config.py:54-183`; Admin mutation is limited by
  `app/api/v1/admin.py:218-256`.
- `docs/ru/10-testing-framework.md:24-111`,
  `docs/ru/testing/scenario-testing.md:11-26` and
  `docs/ru/testing/super-smoke.md:33-52` advertise direct/shared commands,
  nonexistent tasks or root output instead of the safety checks and task-local
  paths in `scripts/verify_local.ps1:83-127`.
- `docs/ru/05-deployment.md:458-678` mixes verified Compose behavior with
  nonexistent production files/images, incomplete mandatory security settings,
  incompatible replica guidance and an invalid point-in-time restore example.
- `docs/ru/03-architecture.md:3-1675` mixes maintained Admin/Simulator surfaces
  with future PWA/WebSocket/operations targets without a front-door status split.
- Simulator frontend/backend protocol prose claims competing source-of-truth
  authority; current OpenAPI owns the REST/SSE schema. The current interaction
  guide describes a direct trustline although the implemented backend-first path
  may route multi-hop.
- Focused local-link resolution found 26 broken targets in the four current RU
  front-door bodies above. A wider scan is dominated by unindexed dated/history
  documents and is not authority to rewrite archives mechanically.
- EN/PL pages generally lack source/synchronization metadata, and EN/PL config
  pages still claim independent source-of-truth status.

### Intended behavior

Readers must reach one current owner for runtime, architecture, REST, config,
testing, Admin and Simulator behavior. Current documents describe verified
commands and separate observed behavior, accepted intent and future target.
Archives remain visibly historical. EN/PL status is explicit without claiming
repository-wide parity.

### Optimal bounded target

- Rebuild the current RU config, testing and deployment guides around executable
  env/Compose/verifier entrypoints; do not implement the prose they replace.
- Put a concise current executable map and a clear target/history boundary at the
  architecture front door rather than rewriting the historical narrative.
- Correct current Simulator authority and multi-hop wording, add the verified
  Phase 5 keyboard/reduced-motion paths, and classify dated/duplicate specs in
  their indexes without deleting archive bodies.
- Correct current Admin index status for the already implemented operator advice
  and Phase 4 paths.
- Mark the known EN/PL config/PWA parity claims as unsynchronized translations;
  do not translate stable-but-unchanged bodies.
- Repair local links in retained changed current documents and validate them from
  each containing file. External URLs and historical-body anchors are not part of
  this bounded scan.

## Verification plan

After each owning micro-batch:

- `git diff --check`, reference scan and `git status --short`;
- focused Simulator unit test for the test cleanup;
- settings/runtime-path tests and PowerShell parse checks for DB relocation;
- two concurrent canonical backend selectors with unique task slugs after pytest
  producer relocation;
- Admin/Simulator Playwright config discovery plus affected unit/build gates;
- Admin fixture sync/validation and strict Simulator UAH sync with a no-diff check;
- Admin unit/build for starter cleanup;
- changed-current-document link/path/command scan;
- full `scripts/verify_local.ps1` milestone because common config, scripts and
  test discovery are changing.

PostgreSQL, OpenAPI, live browser behavior and migrations are not product
contracts changed by the target. The retained duplicate PostgreSQL test is not
deleted without a fresh disposable execution. No published workflow is planned
or authorized, so Phase 6 will not claim `CI green`.

## Rollback

Every cleanup class receives a separate commit. Revert the individual commit to
restore its prior behavior. Runtime relocation never mutates the old root DB, so
the manual legacy `DATABASE_URL` override remains an immediate data-preserving
fallback. Deleted tracked starter files remain recoverable from Git. Existing
ignored artifacts are not part of any commit and are not removed.

## Completion evidence (2026-08-09)

Phase 6 completed only REN-013B, REN-014 and REN-016 from execution base
`d6e3e094d14422e20075a70839b0b288efe51bce` through accepted product HEAD
`90ac5ef6197c184b5818fe52826a8e97dbb5f6d5`.

### Delivered decisions

- Removed only the unobserved Enter dispatch from `GraphNavigator.test.ts`; the
  browser keyboard proof remains. The PostgreSQL duplicate, explicit
  source-policy guard and unique positive-boundary tests were retained.
- Moved default backend SQLite, direct/canonical pytest state and Playwright
  outputs below `.local-run/`; explicit env/`.env` overrides resolve through
  application Settings from repo-root cwd.
- Reset deletes only `.local-run/geov0.db`; legacy/custom URLs fail closed before
  healthy services stop. Root `geov0.db` remained untouched (5,894,144 bytes;
  SHA-256 `7B85C428F6FAD644D716FE953747ED9B767F6C178A51F2332E03AA1339A9E7F3`).
- Classified Simulator v1 as historical/read-only, retained archives and cached
  fixtures, and removed only Admin starter files `HelloWorld.vue`, `vue.svg` and
  `public/vite.svg` plus its Vite favicon reference. Deleted tracked files are
  recoverable with Git; no ignored runtime/user file was deleted or moved.
- Reconciled current runtime/config/testing/deployment/Admin/Simulator docs,
  marked EN/PL config copies unsynchronized and repaired changed local links.

### Verification ledger

- Focused GraphNavigator: exit `0`, 1 file / 3 tests.
- Settings/runtime selector: exit `0`, `60 passed, 1 skipped`; two concurrent
  canonical slugs produced the same counts with distinct DB/pytest/cache/artifact
  roots. Direct-pytest fallback probe: exit `0`, `10 passed`.
- Admin cleanup: exit `0`, `28` files / `154` tests; build, fixture sync and
  validation passed. Reference scan after deletion: zero.
- Playwright discovery: Admin `7` tests / `3` files; Simulator default and HUD
  `24` / `7`; Phase 5 `5` / `1`, all exit `0`. Scoped Chromium smoke: Admin and
  Simulator each `1 passed`; output observed below `.local-run/playwright`.
- Generated fixtures: Admin sync/validate and strict Simulator UAH sync exited
  `0`; no Git diff. EUR/HOUR cached copies were retained, not claimed freshly
  regenerated.
- Full canonical milestone
  `.\scripts\verify_local.ps1 -TaskSlug phase6_full_milestone`: exit `0`;
  backend `777 passed, 3 skipped, 15 deselected`; Alembic head
  `017_add_owner_to_simulator_runs`; Admin lint `0` errors / `116` existing
  warnings, unit `154 passed`, build passed; Simulator lint/typecheck passed,
  unit `701 passed`, build passed. After review remediation the settings selector
  again passed `60`, skipped `1`; PowerShell parse errors were zero and unsafe
  legacy/custom reset delete references were zero.
- Adversarial changed-current-doc scan: `98` local links in `23` Markdown files,
  zero broken. Final scan including the three program files and this ledger:
  `99` local links in `26` changed Markdown files, zero broken;
  `git diff --check` exit `0`.

### Reviews

- Independent adversarial review found reset-protection, canonical-gate and
  Simulator-doc drift; accepted fixes and final deltas were re-reviewed with no
  remaining P1/P2.
- Claude Code CLI `2.1.226`, `opus`, effort `high`, read-only plan mode in a clean
  standalone filesystem-origin clone reviewed exact runtime range
  `d14f73eab7ad636e01c6a4f30d3969c492380aaf..d772d081b77451c97074dcacc81e36d8bb086d40`:
  exit `0`, complete JSON, `is_error=false`, resolved `claude-opus-5`.
- The single remediation review covered
  `24f22aedaaf9be4f548cb961b7bdcc4d4de8f841..c8edccc8fbda112027bb342f6df6830cbfe0a8f6`:
  exit `0`, complete JSON, `is_error=false`, resolved `claude-opus-5`. Its one
  service-control finding was fixed in `90ac5ef` and accepted internally; no
  second external loop was opened.

### Explicit residuals

No live PostgreSQL/Docker/concurrency tier, visual browser matrix, external-link
or Markdown-anchor scan, archive-body rewrite, destructive reset execution, or
published CI run was performed. Old ignored root DB/test/log/PID/NDJSON/report
artifacts remain owner data pending separate cleanup approval. Phase 7/REN-015 was
not started.
