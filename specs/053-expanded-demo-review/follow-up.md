# 053 follow-up — Полный результат клиринга при закрытии линии

- **Date:** 2026-10-29
- **Status:** SOURCE ACCEPTED — 2026-10-29; implementation, targeted/live gates and required external source review passed. Required CI and delivery receipt: [PR #363](https://github.com/slawa19/GEOv0/pull/363).
- **Status authority:** Fresh red/green observations, exact selectors and review records are in the dated acceptance entries below; this label alone proves neither behavior nor delivery.
- **Owner surface:** `app/core/simulator/edge_patch_builder.py`, clearing callers in `app/api/v1/simulator.py` and `app/core/simulator/tick.py`, and named backend/UI behavioral tests. All three production builder callers inventoried; payment executor behavior retained. No financial algorithm, schema, migration, generator or default-policy change.
- **Origin:** Expanded review 053, Astra finding; root PostgreSQL and actual UI-consumer reproductions on `dc7a9054`. P2, AGENTS §19.5 class 2: database debt is correct; published/displayed clearing result is incomplete.

## Problem

При обычном закрытии линии с непогашенным долгом запрос остаётся отложенным. Последующий клиринг погашает долг и завершает закрытие. Событие `clearing.done` пропускает закрытую линию; её удаляет следующее `topology.changed`. Поэтому интерфейс считает неполное снижение долга и сохраняет неверное «было → стало».

## Findings

**F-053-1, проверено 2026-10-29.** `app/core/simulator/edge_patch_builder.py:294–297` собирает закрытую пару для удаления, но не включает её нулевой долг в patches. `simulator-ui/v2/src/composables/realEventPipeline.ts:472–486` рассчитывает clearing change и history debtDrop только по одному `clearing.done`. Последующее удаление (:325–331) меняет links того же snapshot, не исправляя сохранённую пару (`debtChange.ts:75–78`).

Реальный PG-пример: три линии, на каждой долг 50; подписанный запрос закрытия A→B; обычный ручной клиринг. DB после = 0, закрытая линия действительно closed, одно `clearing.done`, затем одно `topology.changed`. В `clearing.done` только две нулевые patches. UI после ОБОИХ событий: `{was:150, after:50, now:0}`; ожидается `{was:150, after:0, now:0}`. Потери долга нет. Это предшествующий 050/052 дефект смежного пути, не заявленная регрессия этих программ.

## Current / Intended / Optimal

- **Current:** база погашает 150; UI приписывает этому клирингу только 100. Текущий общий долг после удаления линии становится 0, но результат последнего клиринга остаётся 150→50. History consumer использует ту же неполную разницу.
- **Intended:** решение 039 D от 2026-10-19 — «было → стало» и событие истории описывают результат именно этого клиринга; более поздние платежи его не переписывают. Закрытие линии не должно терять часть уже подтверждённого погашения.
- **Optimal:** до события удаления передать правдивое post-commit состояние долга всех затронутых этим клирингом известных линий, в том числе закрытых. Переиспользовать существующий patch contract и расчёт UI. Подтверждение брать из БД/результата committed clearing, не из оценки `cleared_amount × число рёбер`. Не превращать это в глобальное изменение семантики отсутствующих линий или всех payment patches.

## Non-goals

Не менять алгоритм клиринга, денежную транзакцию, порядок закрытия, replay IDs, самостоятельный жизненный цикл snapshot или формат SSE. Не строить новый механизм агрегатов/журнал событий. Не исправлять по дороге прежние BACKLOG 050/052, manual disconnect или поддержку старых БД. Не рисовать придуманную линию, отсутствующую в БД.

## Verification plan

1. **Red before fix:** реальный треугольник через существующие helpers и signed close, обычный manual endpoint; проверить DB150→0 и matching complete patches. Отдельно проиграть точные два события через `applyAcceptedRealEvent`, `createPatchApplier`, `useSystemBalance`: после удаления линии `was=150, after=0, now=0`, history debtDrop150. Root уже получил red на финальном assertion; временный probe ошибся формой RecordingSse в первой попытке, эта ошибка не считается продуктовым evidence.
2. **Callers:** automatic, scripted и manual clearing используют одинаковую исправленную семантику. Cancellation recovery проверять на уже существующем supported path 052, не расширять scope на отключённого manual клиента. Добавить behavioral regression в владельца каждого действительно различающегося пути, без копирования одной проверки во все файлы.
3. **Anti-vacuum:** обычный клиринг без закрытия по-прежнему показывает полное снижение; неисполненное/откаченное погашение не публикует ноль; реально отсутствующая пара не создаётся; удаление публикуется после commit и один раз; более поздний платёж не переписывает clearing pair. Не ослаблять существующие guards closed/recreated lines.
4. **Existing selectors:** `tests/integration/test_p026_s4_tick_close_publication_postgres.py`, `tests/integration/test_p034_s2b_interact_clearing_done_is_published_once_postgres.py`, `tests/integration/test_p052_complete_clearing_pass_postgres.py`, `tests/unit/test_p026_s4_simulator_close_lifecycle.py`; UI `src/composables/debtChange.p039d.test.ts` и `src/composables/realEventPipeline.test.ts`, плюс новый regression selector. Backend через `scripts/verify_local.ps1 -TaskSlug p053_closed_line_fix -BackendOnly -BackendSelector <listed paths>`; UI через `npm --prefix simulator-ui/v2 run test:unit -- <listed paths>`. По SSE-контракту — затронутые normalization tests и simulator smoke; финальный milestone после кода по AGENTS §5.
5. **Forbidden false evidence:** только тест существования события, ручной расчёт вместо действительного consumer, mocked successful monetary commit, `cleared_amount` как общий debtDrop, blanket refresh как замена корректной публикации, сужение существующих assertions.

## Tasks

| ID | Task | Status |
|---|---|---|
| F1 | Red regression через реальную БД и настоящий UI consumer; инвентаризация всех clearing callers | [x] Fresh canonical PG6 red and actual PG-wire UI1 red; all three production builder callers inventoried |
| F2 | Минимальная post-commit публикация закрытых именно этим clearing линий | [x] Clearing-only opt-in; known closed pair and actual DB zero; missing/live-incarnation protections retained |
| F3 | Targeted tests, независимое review защищённого SSE slice, milestone и доставка | [ ] Targeted/UI/browser/live gates and internal/external source review complete; PR/required CI/merge delivery pending |

`[x]` completed; `[ ]` in progress; `[!]` not authorized/blocked. The review request authorized the specification; the separate owner instruction «реализуй» on 2026-10-29 authorizes this implementation wave.

## Changelog

- **2026-10-29:** root reproduced the ordinary signed-close/manual-clearing case on PostgreSQL, then both production UI event handlers. Logs and exact temporary test sources retained under `.local-run/expanded-review-20261029/`; findings consolidation remains in the parent review specification.

- **2026-10-29 — implementation authorization, before code:** owner «реализуй» starts F1–F3 and R1–R3. Root accepted the two existing specifications; independent owner surfaces may execute in parallel. No recorder regeneration, documentation cleanup wave, financial algorithm, schema or default changes. New code must pass meaningful red/green and the stated controls; one integrated milestone and required external Claude review, then PR/CI/merge autonomously.

- **2026-10-29 — implementation and local targeted acceptance:** logical F commit `5b5aa6a7`, integrated source candidate `5b5aa6a7` (R commit `04ea879d`, pre-code authorization `09628d13`). `EdgePatchBuilder.build_edge_patch_for_pairs` defaults to the previous payment behavior; manual clearing opts in through `_emit_interact_clearing_done_best_effort`, and automatic/scripted/supported052 recovery through `RealTick._clearing_patches`. A final patch requires an existing closed DB line and zero aggregated debt; live incarnation precedence, signed availability, precision, post-commit done/removal order and existing liveness/epoch guards remain unchanged. No additional DB query or wire shape.
- **2026-10-29 — fresh red/green:** before source changes canonical new PG selector failed6 cases (11.77s, exit1); manual DB150→0/closed/removal assertions passed but done had2/3 patches. Exact actual PG events through production UI consumers failed1 case: before150/after50, expected after0, while current0 already passed. Final developer F cohort52passed/1slow deselected, OpenAPI23passed. Integrated canonical `verify_local.ps1 -TaskSlug p053_integrated -BackendOnly -BackendSelector <selectors below>` on the source candidate:184passed/1slow deselected98.68s, exit0, Alembic head037. Selected F/R modules, builder, money-resolution, real SSE smoke and OpenAPI contract; this is targeted acceptance, not a full backend tier.
- **2026-10-29 — UI and counterchecks:** affected `closedClearing.p053.test.ts`, `debtChange.p039d.test.ts`, `realEventPipeline.test.ts`, `normalizeSimulatorEvent.test.ts`:56passed; typecheck/build exit0 (build retains its ordinary chunk-size warning); console-guard Playwright smoke1passed3.8s, exit0, task port24841. Separately reran new UI selector with `P053_CLEARING_WIRE` pointing to the fresh integrated PG artifact:1passed,150→0/current0/history150/later-payment pair preserved. Standalone fallback tests consumer contract only; the PG-wire run proves the producer/consumer combination. Actual artifact contains3 zero patches followed by1 removal. Opt-in/default matrix covers absent, requested/nonzero, closed-zero, contradictory closed/nonzero and recreated/live/nonzero; the contradictory case is a renderer guard, not a supported monetary writer claim. Partial failure/cancellation leaves uncommitted debt nonzero. Existing026 recreate barriers and052 recovery failure controls passed. Manual disconnect fallback remains outside scope.
- **2026-10-29 — evidence owners and review:** exact red snapshots, base owner blobs, hashes and logs remain under `.local-run/worktrees/p053-f/.local-run/p053_f_evidence/`; saved red PG source reconstructs only the later artifact-root import/path change, preserving the original6-case logic. Integrated raw backend log: `.local-run/worktrees/p053-implementation/.local-run/p053-integrated/backend.log`; fresh wire: `.local-run/worktrees/p053-implementation/.local-run/test-runs/p053_integrated/artifacts/p053/manual.json`; UI logs under the same worktree `.local-run/p053-ui/`. Pinned Ruff0.1.14 touched production sources and diff-check exit0. Independent read-only internal review on exact candidate: SOUND/class1=0/no findings; root separately inspected diff and fresh artifacts. Reviewer executed no gates. External exact-range record is `.local-run/codex-review/p053-closing-20261029/`; live milestone and external result are recorded after completion, without treating pending work as passed.

Integrated targeted selectors (passed together, with process-local DEBUG=false and runner-derived isolated PostgreSQL DB):

```text
tests/integration/test_p053_closed_line_clearing_patches_postgres.py
tests/integration/test_p026_s4_tick_close_publication_postgres.py
tests/integration/test_p034_s2b_interact_clearing_done_is_published_once_postgres.py
tests/integration/test_p052_complete_clearing_pass_postgres.py
tests/unit/test_p026_s4_simulator_close_lifecycle.py
tests/unit/test_edge_patch_builder.py
tests/unit/test_p053_restart_tick_bound_state.py
tests/integration/test_p053_restart_tick_bound_state_postgres.py
tests/unit/test_run_lifecycle_per_run_scenario_isolation.py
tests/unit/test_p028_e1_run_lifecycle_entries.py
tests/unit/test_p049_s2a_fractional_action_budget.py
tests/integration/test_p034_s1_restart_repeats_the_idempotency_key_postgres.py
tests/unit/test_tick_money_phase_resolution.py
tests/integration/test_simulator_sse_real_smoke.py
tests/contract/test_openapi_contract.py
```

- **2026-10-29 — final local milestone:** `verify_local.ps1 -TaskSlug p053_live -BackendOnly -IncludeExpensive -BackendSelector tests/integration/test_p050_realistic_scenarios_live_postgres.py::test_realistic_economy_survives_100_completed_ticks_and_restart`:2passed426.79s, exit0/head037. Both realistic scenarios completed100 ticks and25 after stopped restart, each with300+75 normal successes,0 failures, no stall ticks and4+1 clearing events. Actual two report files/four launch epochs inspected under the integrated worktree `.local-run/test-runs/p053_live/artifacts/p050-live/`; raw log `.local-run/p053-live/live.log`. Source/tests remained `5b5aa6a7`; no money/default/threshold or measurement-guard changes. Full local tier was intentionally not repeated: root accepted targeted+live acceptance; required CI owns final full backend/tooling/UI tiers.
- **2026-10-29 — required external review:** one normal-p/JSON-schema invocation, Claude Code2.1.312, requested opus/high, resolved `claude-opus-5-5` only, exact frozen `7c9831da174749a7945870da78b4c2b7505c3d2c..5b5aa6a7703de47fd6b236ce85435948ba40b3e2`. Clean standalone clone/local verified bundle; plan permissions, Edit/Write/NotebookEdit denied. Exit0; VALID `VERDICT-P053: SOUND`, `REVIEW-COMPLETE: YES`, class1=0,10/10 unique checks, source closure allowed. UTC10:36:23.296–10:40:49.644; CLI list-basis cost2.4171946USD, not verified billing. External reviewer ran no tests and could not read outside-clone red artifacts; local/live results remain separate evidence. Prompt/source frozen separately with hashes in invocation metadata; no fix-delta or additional Argon call. Argon expanded-review request was already completed; no unresolved substantive P1/P2 disagreement remained.

| Implementation review | Independent result | Verification/disposition |
|---|---|---|
| Internal strong read-only reviewer | SOUND, class1=0; no findings | Independently traced callers/guards and opened actual red artifacts; no gate execution claimed |
| Claude P053-CR-1 | P3/class2, unexecuted recreation-during-SELECT display race | Source mechanism checked: stale closed patch can apply by pair key while removal guard preserves recreated line. No DB/money effect or reproduction claimed; dated recipient [BACKLOG053-5](../BACKLOG.md#053-5), no source change/new round under §19.5 |

Required CI and delivery remain owned by the main session; no CI run, PR or merge is claimed in this local acceptance record.

- **2026-10-29 — delivery tracking:** [PR #363](https://github.com/slawa19/GEOv0/pull/363) owns the final commit, required CI job results and merge receipt. Local acceptance above is source acceptance; PR state is authoritative for delivery.
