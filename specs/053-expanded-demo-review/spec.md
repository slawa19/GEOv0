# 053 — Расширенное ревью демо после 050 и 052

- **Date:** 2026-10-29
- **Status:** REVIEW COMPLETE — findings consolidated and follow-up specified; product implementation is not started.
- **Status authority:** Findings require current source evidence and root verification; a reviewer verdict alone does not prove correctness or delivery.
- **Owner surface:** Read-only 050/052 and their functional callers/consumers; this slice changes only review records and, if warranted, a follow-up specification.
- **Authorization:** Владелец 2026-10-29 запросил полное расширенное ревью затронутых срезов и смежного функционала двумя агентами — Astra и Gemini Argon — с тщательной перепроверкой Argon, без крайних случаев и overengineering; затем консолидацию подтверждённого и вывод либо спеку доработки.

## Problem

Перед пользовательской приёмкой проверить не только diff, но и связность обычных сценариев: генерация сети → планирование → денежное исполнение → клиринг → SSE → граф; ручные действия, pause/stop/restart и изоляция прогонов. Прежние положительные ревью и gates — ограниченное evidence, не замена этому расширенному чтению.

## Frozen scope

BASE `947aadb91a9f6951d3fd1f5a7b667f875cd5fcf6`, HEAD `dc7a905453bd8b03d971c2d660bdcff82e0c888c`.
Product slices 050/052; 051/cleanup procedures only if they materially affect the reviewed contract.
Adjacent owners: payments/router policy, clearing runner/service, simulator lifecycle/manual operations, SSE/replay/snapshot and simulator-ui/v2 consumers, scenario generators/recording and measurement/test validity.

## Non-goals

No product edits during review; no speculative hardening, new architecture, old database compatibility, blanket test reruns or reopening accepted P3 debt without new evidence. Default clearing-demo-10, cadence 25, soft cutoff disabled and emergency timeout 60 s are accepted owner decisions.

## Findings

**EA-1 — P2, класс 2; CONFIRMED.** Завершение закрытия линии при клиринге исключает её из `clearing.done.edge_patch` (`edge_patch_builder.py:294–297`), но удаляет только следующим событием. UI рассчитывает before/after и history debtDrop на первом событии (`realEventPipeline.ts:472–486`); удаление сохраняет snapshot identity. Root воспроизвёл обычным signed close и manual clearing: DB150→0, после обоих UI-событий `{was:150,after:50,now:0}` вместо `{was:150,after:0,now:0}`. Это pre-existing до 050/052, не нарушение денежной транзакции. [Узкая спецификация доработки](follow-up.md).

**ARG-2 — P2, класс 2; CONFIRMED WITH CORRECTIONS.** Остановленный restart обнуляет номер такта, но сохраняет viz-helper прошлого запуска; источник `run_lifecycle.py:581–606`, `viz_patch_helper.py:65–70`. Debug-only exact-method probe: старый refresh91, новых запросов refresh на1–100 =0, на101 =2; reset-control на1 =2. Это устаревшая шкала визуализации, не буквальная заморозка всех цветов/размеров и не денежная ошибка. Сохраняемые decay/no-progress markers имеют условные эффекты; пример Argon о включённом decay в default demo неверен. [Отдельная спецификация restart](restart-follow-up.md).

**ARG-3 — P3, класс 2 по решению root; CONFIRMED CONDITIONAL MAINTENANCE.** Рецепт записи исполняется на новой сети, но его fresh result расходится с числами проверок прежних assets. Actual4 cycles/11 edges/3 shared/175.00 против expected3/8/1/247.95; прогноз Argon7 или9 рёбер опровергнут. Текущие публичные плейлисты не менялись и не объявляются сломанными. [Условная задача перед следующей регенерацией](recording-follow-up.md).

**ARG-5 — P3, класс 2; CONFIRMED DOC DEBT.** `docs/ru/simulator/scenarios-and-engine.md:633` сохраняет «штатный5000мс», а :388 — неиспользуемый MAX_DEPTH; default0/hard60 подтверждён кодом и :371. Две локальные правки при следующем изменении справки, без нового code slice.

## Consolidation

| Candidate | Who | Root verification | Decision |
|---|---|---|---|
| EA-1 / ARG-1 | Astra и Argon независимо | Real PG signed-close/manual-clearing + actual TS pipeline, final state150/50/0 instead150/0/0; controls pass | One P2/class2 finding. Upstream post-commit patch fix proposed. Не принимать совет Argon вывести zero из отсутствия patch: отсутствующее измерение не ноль. Refusal measure_live при неполных patches — правильный fail-closed, guard не ослаблять. |
| ARG-2 | Argon; targeted Astra cross-check after independent round | Source bodies and debug-only dependency-injected stopped restart; source reviewed by root | P2/class2 stale quantiles; decay/default-demo claim corrected, counter effect conditional. Не accepted active-restart race. |
| ARG-3 | Argon | Fresh canonical real-PG recipe, saved actual response compared with exact current UI assertions | Root P3/class2 for next regeneration; actual numbers replace incorrect forecast. Не ослаблять money validator и не регенерировать fixtures сейчас. |
| ARG-4 | Argon | Source directories/UUID/evidence intent checked, no runtime growth measurement | Reject as isolation/mandatory-delete defect. Empty candidate directory is optional hygiene; retained balance evidence stays useful. No TTL framework or cleanup wave. |
| ARG-5 | Argon | Direct source/doc comparison; distinct from accepted052-4 test prose | Accept P3/class2 docs debt, two local corrections on next touch. |

Новых воспроизведённых денежных нарушений класса1 не найдено в просмотренной области; это не доказательство отсутствия любых ошибок. Два независимых reviewer verdicts — WEAK/class1=0. Программы050/052 не переоткрываются. Priorities: UI result publication → stopped-restart refresh → recording compatibility only before regeneration; P3 prose does not block owner viewing.

## Current / Intended / Optimal

Current: денежное состояние корректно, объяснение результата клиринга при штатном закрытии линии неполно. Intended: принятое решение 039 D — показывать результат именно клиринга. Optimal: полная post-commit публикация затронутых этим клирингом известных линий до их структурного удаления, без нового API/алгоритма; точные границы и counterchecks — follow-up.

## Verification plan

- Two independent readings, both covering the full scope; concrete path:line, reachable trigger, current/intended/optimal, severity and AGENTS §19.5 class for every candidate.
- Root verifies every P1/P2 by a minimal executable observation where feasible; source-only hypotheses remain explicitly unverified. Candidate reproductions are debug-only unless run through the canonical selector. No green claim from a check that did not execute.
- Money/ledger atomicity, trust limits/policy, lifecycle isolation, correct final SSE patches and honest measurement must survive. Existing selectors are selected only after a candidate identifies its owner; no full gate for unchanged code.
- Consolidation records origin, duplicates, corroboration, rejection, accepted debt and decision. No new verification framework; only concrete confirmed causes justify a follow-up implementation spec.

## Tasks

| ID | Task | Status |
|---|---|---|
| T5301 | Independent Astra and Argon review of frozen scope | [x] Both reports complete; Argon model identity limitation below |
| T5302 | Root verification and consolidation of all findings | [x] Confirmed, corrected, downgraded and rejected candidates separated |
| T5303 | Conclusion or scoped follow-up spec; reviewer usefulness ledger and local records | [x] Two focused product proposals, one conditional tooling proposal; no product changes |

`[x]` completed; `[ ]` in progress; `[!]` not authorized/blocked.

## Changelog

- **2026-10-29, before Argon invocation:** explicit owner request, also trigger 3 (final expanded review of a substantial delivered batch) and trigger 2 (config, clearing cancellation and SSE integrity). Read current usefulness ledger: Argon has added useful UI/caller findings but missed monetary verification defects, and opencode does not confirm resolved model ID. Launch one independent `openrouter/google/gemini-4-argon` review via opencode 1.19.0 plan/pure in a standalone clean clone from a local bundle at frozen HEAD. Requested model is not resolved-model evidence; markers, process exit, cost and coverage are recorded separately. Astra requested `gpt-6-astra`, high, by explicit user override of the usual role table. No third reviewer is requested for this analysis-only round.
- **2026-10-29, root corroboration EA-1:** canonical temporary PG selector `tests/integration/test_p053_review_probe.py`, `-TaskSlug p053_close_probe -BackendOnly -Python D:\Work\Projects\GEOv0\.venv\Scripts\python.exe`, process-local DEBUG=false/TEST_DATABASE_URL unset/PYTEST_ADDOPTS=-s: exit1, `1 failed in 7.37s`, exact `UI clearing after=50.00; actual database after=0; before=150`. First attempt was a probe-only `KeyError: 'payload'` and is not product evidence. Actual UI consumer test via `npm --prefix simulator-ui/v2 run test:unit -- src/composables/p053.review-probe.test.ts`: exit1, one failed, both clearing.done and topology.changed processed, actual after50/expected0. Existing backend selector `test_p026_s4_tick_close_publication_postgres.py::test_an_interact_clearing_that_completes_a_close_removes_the_edge_once`: exit0, 1 passed6.84s, head037; existing UI `debtChange.p039d.test.ts`: exit0,13 passed. Temporary probes were moved out of test discovery after execution; exact sources/logs/hash receipt retained in `.local-run/expanded-review-20261029/`. No product changes or full gate runs.
- **2026-10-29, Argon completion:** opencode1.19.0, `opencode run --pure --agent plan --model openrouter/google/gemini-4-argon --format json`, prompt supplied on stdin; UTC08:44:56.8468055–09:13:32.4592948, exit0, final reason stop, all3 requested markers present. Cost **5.5985805 USD**, sum of59 step_finish.cost entries, not independently verified billing. Requested model specified; resolved model is not confirmed by this protocol, so this is source advice with manually checked completion, not a formal resolved-model gate. Standalone clone remained clean at frozenHEAD; no filesystem sandbox guarantee is claimed. Prompt hash unchanged. Extraction wrote the complete UTF-8 report; its first console print failed only with local cp1252 UnicodeEncodeError, then the intact file was read as UTF-8. No second paid call.
- **2026-10-29, remaining verification:** ARG-3 canonical `-TaskSlug p053_recipe_probe -BackendOnly -BackendSelector tests/integration/test_p050_workbench_recording_recipe_postgres.py -Python <root venv>`: exit0,1passed10.41s, head037, saved response and actual/expected comparison. `argon2-debug.py` executes unmodified AST method bodies with fake dependencies, exit0: debug-only, not HTTP/PG acceptance. Reports `astra-report.md`, `argon-report.md`, `argon-crosscheck-astra.md`; raw events, invocation metadata, exact probes, logs and receipts in `.local-run/expanded-review-20261029/`. No full backend, live100/restart, browser E2E or CI runs in this review; existing historical gates are not relabelled fresh.

- **2026-10-29 — subsequent implementation authorization:** after delivery in PR #362, owner «реализуй» starts only the two user-facing follow-ups F1–F3 and R1–R3. The completed review remains historical evidence; new implementation results belong to their follow-up files. Recorder/prose P3 work is not part of this wave.
