# 052 — Complete automatic clearing passes

- **Date:** 2026-10-27
- **Status:** AUTHORIZED — design accepted by main on 2026-10-27; implementation follows the committed plan.
- **Status authority:** Completion is established by the success criteria and retained evidence below, plus PR delivery; this label does not establish passing gates or delivery.
- **Owner surface:** `app/config.py`, `app/core/simulator/tick.py`; the named tests and three active simulator documents below. No clearing algorithm, ledger, router, schema, UI implementation or fixture changes.
- **Origin:** After delivery of 050, the owner explicitly approved: «Для демонстрации полного взаимозачёта ... завершать проход, оставляя более длинный аварийный таймаут — да, сделай». Main owns final decisions, commits, push, PR and merge. Cleanup is a separate root-owned stream and is excluded here.

## Problem

The automatic demo pass normally stops starting cycles after five seconds. In 050, a separately recorded manual pass without a deadline completed 38 / 70 cycles for village / town, reducing actual edge debt by 31101.86 / 34120.41; runner intervals were 5.547 / 9.563 seconds. These are different runs and debt IDs, with shared-host load, not a paired benchmark or SLA. Evidence owner: `.local-run/test-runs/p050_manual_clear_diag/{actual-summary.json,canonical.log}`; source was `0f773fcc`, and relevant product paths were delivered unchanged in main `1cf4e135`.

The owner now prefers completing the normal pass to the former latency limit. A distinct emergency timeout remains necessary as an operational escape, not as a financial invariant. A cancelled automatic pass already accounts for durable commits but deliberately publishes no debt patches, so the UI cannot reconstruct that event's before/after debt movement from its summary alone.

## Findings

1. **Policy mismatch after the new owner decision.** `app/config.py:283–284` defaults to 5000 ms / 8 s; `tick.py:1214,1265` forces a positive soft deadline. Changing only the budget default to zero would still impose a 1 ms deadline.
2. **Coupled emergency limit.** `tick.py:888–895` derives `max(2 s, 4 × budget)` capped by the setting. Raising only the cap to 60 seconds would leave a zero-budget run with a two-second timeout.
3. **Partial progress reaches SSE without debt patches.** The committed callback records amount and touched pairs synchronously (`tick.py:1230–1246`), but cancellation (`1356–1381`) emits `node_patch=None`, `edge_patch=None` and skips growth. This is source-confirmed; the new DB-to-SSE reproducer must fail before implementation. UI consumption at `realEventPipeline.ts:472–488` computes before/after debt around patches.

## Current / Intended / Optimal

**Current.** Automatic cadence (`tick.py:986–1004`) and scripted clearing (`1025–1026`) share `_execute_clearing_with_timeout` → `_run_clearing` → the existing common runner. Runner deadlines are cooperative between cycle starts and deliberately permit the first attempt (`app/core/clearing/runner.py:486–510`). Normal progress is patched and emitted before trust growth (`tick.py:1318–1353`). Emergency cancellation drains the task and preserves committed volume, while the partial event lacks patches. Existing p023d tests prove committed amount/volume survival, but do not prove these patches. Its default test with two 300 ms occurrences cannot distinguish 5000 ms from disabled.

**Intended (owner, 2026-10-27).** Normal automatic passes finish without the five-second soft cutoff; the independent emergency limit defaults to 60 seconds. Cadence 25, visitor default `clearing-demo-10`, scenarios, prices, frozen 050 thresholds and all financial/transaction invariants remain unchanged. Completion means the common runner's own successful pass result, not that every debt becomes zero; existing stop, replan and operational-limit outcomes remain real interruptions.

**Optimal target.** Keep the existing soft-budget setting for compatibility and explicit bounded operation: default 0, nonpositive values disable its deadline, positive milliseconds opt in to the existing cooperative cutoff. Pass `deadline=None` when disabled. Remove the multiplier/cap coupling: the hard-timeout setting itself supplies the independent limit, default 60 seconds, with the existing 0.1-second defensive floor. Keep centralized lenient integer parsing; no new config family or driver policy. The grace for a genuinely pending prior clearing remains derived from the independent hard timeout, so its default changes from 4 to 30 seconds; no extra pending-clearing mechanism is introduced.

On cancellation after a confirmed commit, attempt bounded publication of DB-backed patches using the existing calculator and a fresh read session. Publish known durable financial progress and closed-line events before considering any optional trust growth. Preserve cancellation, the exact committed volume, one-event-only ordering and task drainage; if cancellation lands after `clearing.done`, finish only any still-unpublished closed-line notification without replaying that event. Recovery is limited to a small fixed cleanup bound (proposal: 2 seconds), not an extension of clearing execution. No new occurrence starts and no trust growth is performed during emergency cleanup. If recovery fails, times out or is cancelled again, retain the explicit patchless summary fallback and diagnostic log; never fabricate patches from cycle amounts or claim they were delivered. A cancellation after the event was already emitted cannot emit it twice.

**Alternative rejected.** Removing the budget field would break explicit bounded deployments/tests and require a broader compatibility change without improving the requested default. Merely changing defaults fails because the adapter forces a deadline and the emergency formula remains coupled. Shielding the whole clearing pass or starting another clearing pass during cleanup would defeat cancellation and is excluded.

**Reachable siblings and boundary.** The manual simulator action (`app/api/v1/simulator.py:2050–2068`) already calls the runner without a deadline and has no automatic emergency wrapper. Its request-cancellation summary is also patchless; this separate pre-existing manual-disconnect limitation is not claimed fixed here. Normal manual publication, global `/clearing/auto`, periodic maintenance, runner leases, commit resolution and algorithms are unchanged. Automatic and scripted paths must both inherit the new adapter policy. No API/SSE names, shapes, aliases or required fields change; existing optional patches become available on the recovered cancellation path.

## Owner surface

- Production implementation: `app/config.py`, `app/core/simulator/tick.py` only. `real_runner_impl.py:142` already captures the soft setting; verify propagation without adding a second source of truth. `app/core/clearing/runner.py` and `service.py` are read-only dependencies.
- Tests: `tests/integration/test_p052_complete_clearing_pass_postgres.py`; `tests/integration/test_p023_d_tick_driver_through_runner_postgres.py`; `tests/integration/test_p034_s2_clearing_done_amount_is_in_the_equivalents_step_postgres.py` (replace only the stale patchless control, preserving all precision assertions); `tests/integration/test_p050_realistic_scenarios_live_postgres.py` (runtime parameter witness 0 / 60 only, frozen acceptance unchanged); narrow affected unit expectations if measured.
- Active docs: `docs/ru/simulator/backend/real-mode-runbook.md`, `docs/ru/simulator/scenarios-and-engine.md`, `docs/ru/simulator/realistic-scenarios.md`; this spec, registry and required review ledger / accepted-debt receipts.
- Every additional caller correction requires its own observed mismatch and dated CIO before editing. No blanket test rewrites.

## Non-goals

No clearing algorithm, capacity/routing, ledger or commit-resolver redesign; no schema/OpenAPI changes, migrations, new service/background loop/framework, cache, observer protocol or UI implementation. No fixture regeneration, prices, decay, thresholds, seeds, cadence or default-scenario changes. No historical/archive edits. No cleanup/deletion/policy bypass; root owns that separate stream. No new optimization or seed search based on the ensuing live result.

## Proportionality (AGENTS §19.2)

1. Prevent the observed demo truncation and patchless display of already-durable debt movement; do not label the five-second limit a protocol requirement or assert a reproduced money loss from it.
2. The protocol requires atomic, limit-respecting clearing; it does not prescribe a five-second pass. This change is the owner's demo policy; existing transaction/hold/refusal contracts remain the owners of financial safety.
3. There is no hostile in-process adversary. The emergency timeout detects unresponsive operational work; cancellation tests protect ordinary defects and ordering.
4. Reuse nullable runner deadline, existing settings, existing patch calculation and committed callback. Removing the field or adding a workflow/event framework is larger and unnecessary.
5. Planned cost: zero tables/services/modules in production; two existing production files, approximately 60–100 changed production lines before measurement. Recovery adds post-commit reads only on cancellation; count and timing must be reported from the final diff / tests, not claimed from this estimate. A slow default regression may spend about 9 seconds; full live/backend milestones each have one owner.
6. Include real reachable timeout after a commit, no-progress cancellation, cancellation during patch calculation/after event publication, repeated cancellation and unavailable recovery reads. Exclude legacy-database states, unrelated manual-disconnect redesign and hypothetical capacity changes.

## Verification plan

**Red before source edits.**

- Real automatic pass through existing runner and PostgreSQL: a fixed nonempty graph with successive occurrences spanning the former 5 / 8 seconds must finish on defaults. Use controlled runner deadline clock for cheap boundary proof and a narrow `slow` real-duration case beyond 8 seconds; do not accept a settings-only assertion or the old 2 × 300 ms witness as sufficient.
- Positive soft-budget anti-vacuum control on the same graph must still interrupt between occurrences and retain exact durable progress. Hard timeout must be independent for soft budgets 0, 250, 5000 and 60000.
- First real occurrence commits; second is held until a small explicit emergency timeout. Assert exact DB debts, one `clearing.done`, nonempty correct node/edge patches, exact committed amount/volume, untouched remaining cycle, no growth and no attached task. Current patchless branch must fail it.
- Recovery read failure/hang, repeated cancellation, zero progress, cancellation while patches are read and after publication: bounded exit, cancellation preserved, no duplicate/missing amount, no orphan finalizer. Patchless fallback must be observed as fallback, never equated with successful patches.

**Named existing canonical selectors.**

Cheap batch uses `scripts/verify_local.ps1 -TaskSlug p052_targeted -BackendOnly -BackendSelector` with exact paths: `tests/integration/test_p052_complete_clearing_pass_postgres.py`; `tests/integration/test_p023_d_tick_driver_through_runner_postgres.py`; `tests/integration/test_p034_s2_clearing_done_amount_is_in_the_equivalents_step_postgres.py`; `tests/integration/test_p034_s2b_clearing_done_reads_the_precision_before_the_pass_postgres.py`; `tests/integration/test_p034_s2b_interact_clearing_done_is_published_once_postgres.py`; `tests/integration/test_p039_dfix_clearing_done_precedes_trust_growth_postgres.py`; `tests/integration/test_p036_b1_fixdelta_postgres.py`; `tests/integration/test_static_clearing_hard_timeout_no_leak.py`; `tests/unit/test_tick_pending_clearing.py`; `tests/unit/test_tick_clearing_publishes_progress.py`. Preserve bounded 250 ms / small explicit hard-timeout safety stands; pin their intended timeout locally rather than inheriting the new default.

After stable source/internal adversarial review: one canonical expensive regression plus both realistic 100-tick/restart scenarios via the existing live module, same frozen 42 / 15 / 100 and criteria. Capture actual 0 / 60, complete/interrupted statuses, remaining cycles, DB-to-SSE edge changes, per-window shares, role/reject/assigned, sampled debt/limit maxima and phase/full-tick timings. Report real emergency interruptions honestly. One owner, isolated DB; no duplicate live loop.

A full canonical backend milestone and required CI run only after source freeze; no UI implementation change, so reuse completed 050 UI evidence only for unchanged source, and run the mandatory affected SSE consumers: `npm --prefix simulator-ui/v2 run test:unit -- src/api/normalizeSimulatorEvent.test.ts src/composables/realEventPipeline.test.ts src/composables/realFx/useRealClearingFx.test.ts`; canonical backend selector `tests/integration/test_simulator_sse_real_smoke.py`; and `npm --prefix simulator-ui/v2 run test:e2e:smoke` with isolated runtime/ports. UI dependencies must be verified from actual installed contents, reusing the complete E2E tree only after manifest/lock identity checks. Exact final selectors are frozen with the implementation diff. One mandatory Claude closing review on committed credential-free `BASE..HEAD`, known working normal `-p` + JSON Schema, Opus/high; no slash-command retry. At most one justified material fix-delta review. Review findings classified by §19.5 before tasks.

**Forbidden proof.** No synthetic ledger changes instead of real committed occurrences; no fake patches, mock-only money evidence, timer/status assertions without debt effect, selecting a passing seed, reducing frozen thresholds, omitting bounded-mode controls, or treating cancelled/absent measurements as zero. Do not call `complete` equivalent to all debt zero or treat the historical manual times as a guarantee.

## Tasks

Legend: `[x]` completed with evidence; `[ ]` active; `[!]` waiting for dependency. Entire follow-up is owner-authorized; main accepted this plan before source edits.

| ID | Task | Status |
|---|---|---|
| T5201 | Accept scope/CIO, freeze design and authorization before source edits | [x] Main accepted 2026-10-27 |
| T5202 | Red reproducer → independent timeout policy → bounded partial-progress publication; cheap canonical checks and internal review | [!] T5201 |
| T5203 | One affected live100/restart milestone, stable full backend and required CI evidence; sync active docs | [!] T5202/source freeze |
| T5204 | Mandatory frozen external Claude closing, classify findings, delivery receipt | [!] T5202/source commit; may overlap gates |

## Changelog

- **2026-10-27 — main accepted design before source edits.** Default nullable deadline, independent 60-second emergency timeout, bounded recovery with explicit fallback and mandatory affected UI consumer/smoke checks accepted. Existing runner completion/operational limits remain authoritative; no claim of zero total debt. Cleanup permissions are a separate stream.
- **2026-10-27 — authorization/design draft before implementation.** Main relayed the explicit owner approval after delivered 050 (PR #359, main `1cf4e135`). Next free program number checked: 052. Isolated integration branch `codex/052-complete-clearing`; no source edits or new runtime/tests performed. Read-only independent strong reviewer confirmed the forced 1 ms zero-budget trap, coupled emergency formula and automatic/manual patchless cancellation boundary. Main acceptance is required before implementation; cleanup remains separate.
