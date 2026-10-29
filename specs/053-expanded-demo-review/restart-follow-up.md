# 053 follow-up — Сброс привязанных к тактам кэшей при restart

- **Date:** 2026-10-29
- **Status:** SOURCE ACCEPTED — 2026-10-29; implementation, targeted/live gates and required external source review passed. Required CI and delivery receipt: [PR #363](https://github.com/slawa19/GEOv0/pull/363).
- **Status authority:** Dated acceptance below distinguishes original debug-only evidence from fresh public-runtime/PG red/green, live100+restart and independent reviews; status alone proves neither behavior nor delivery.
- **Owner surface:** `app/core/simulator/run_lifecycle.py`, focused lifecycle/viz behavioral tests. No money algorithm, DB reset, new lifecycle service or changes to the accepted active-restart concurrency boundary.
- **Origin:** ARG-2, verified after independent review; P2, AGENTS §19.5 class 2. Owner authorized implementation on 2026-10-29 after the completed review.

## Problem

Restart обнуляет `tick_index`, но оставляет визуальный helper с номером обновления прошлого запуска. При старом `_last_refresh_tick=91` он не обновляет пороги на новых тактах 1–100: размер/цвет/толщина продолжают рассчитываться по старому распределению, хотя деньги и лимиты уже меняются.

## Findings

`run_lifecycle.py:581–606` обнуляет такты и часть последовательных счётчиков, но не `_real_viz_by_eq`. `viz_patch_helper.py:65–70` использует разность нового и старого номера такта. Debug-only probe остановленного прогона: после настоящего тела restart метка91 сохранилась; на1–100 запросов обновления0, на101 —2; контроль со сброшенной меткой на1 —2. Не нужны гонка, отказ БД или активный старый tick.

Смежные поля той же границы: `_edge_clearing_history.last_clearing_tick` и `_real_consec_money_no_progress_ticks` также остаются. Первое может пропустить один decay на совпавшем номере такта только при включённом decay; второе переносит уже накопленную последовательность конфликтов, но успешная денежная фаза его обнуляет. Argon ошибочно привёл clearing-demo-10 как пример включённого decay: у него такой настройки нет, у realistic-v2 decay0. Эти условные эффекты не выдаются за поломку обычного restart или денег.

## Current / Intended / Optimal

Current: номер такта начинается заново, зависимые отметки частично принадлежат прежнему запуску. Intended: долговое состояние сохраняется, но кэш и последовательные счётчики нового запуска не блокируются старым номером. Optimal: минимальный явный сброс доказанных полей в существующем restart, сохранение долгов и содержательной истории доверия. Не очищать всю историю trust drift ради одного tick-marker.

## Non-goals

Не чинить принятые 036-2/049-5 гонки активного restart; не отменять денежные операции и не добавлять orchestration layer. Не расширять работу на гипотетические состояния или менять финансовые эффекты restart.

## Verification plan

- Новый behavioral red: остановленный run с реально заполненным viz-helper → публичный runtime restart → первый новый tick с изменёнными данными обновляет quantiles. Assert observable fresh quantiles, не только `.clear()`.
- Counterchecks: restart сохраняет деньги/долги; обычный refresh до restart сохраняет период10; старый helper не обслуживает новый epoch. На уже имеющемся тестовом стенде проверить только названные связанные поля, не добавлять отдельную систему epoch.
- Существующие селекторы: `tests/unit/test_run_lifecycle_per_run_scenario_isolation.py`, `tests/unit/test_p028_e1_run_lifecycle_entries.py`, `tests/unit/test_p049_s2a_fractional_action_budget.py`, `tests/integration/test_p034_s1_restart_repeats_the_idempotency_key_postgres.py`; targeted live100+restart milestone — `tests/integration/test_p050_realistic_scenarios_live_postgres.py` с `-IncludeExpensive`, если меняется проверяемый им путь. Backend — canonical `scripts/verify_local.ps1 -TaskSlug p053_restart_fix -BackendOnly -BackendSelector <listed paths>`; debug-only AST probe не заменяет этот gate. Дополнительные владельцы определить перед реализацией, не вместо этих контролей.
- Запрещено: выдавать fake-session probe за PostgreSQL/browser acceptance; тестировать только факт вызова clear; удалять историю доверия или менять ожидания долгов ради зелёного теста.

## Tasks

| ID | Task | Status |
|---|---|---|
| R1 | Owner-selector inventory и meaningful red на stopped restart | [x] Fresh canonical PG quantiles/conditional-decay red through public runtime restart; consumers inventoried |
| R2 | Минимальный сброс названных tick-bound полей, сохранение durable состояния | [x] Viz cache, clearing tick markers and no-progress counter reset; debts/committed totals/substantive history retained |
| R3 | Targeted verification, независимое review и соответствующий milestone | [ ] Targeted/live gates and internal/external source review complete; PR/required CI/merge delivery pending |

`[x]` done; `[ ]` in progress; `[!]` not authorized/blocked.

## Changelog

- **2026-10-29:** ARG-2 сужен по проверке: stale quantiles — основной подтверждённый эффект, decay/counter — условные последствия; неверный пример default demo отклонён. Проба и ограничения — в консолидации 053.

- **2026-10-29 — implementation authorization, before code:** owner «реализуй» starts F1–F3 and R1–R3. Root accepted the two existing specifications; independent owner surfaces may execute in parallel. No recorder regeneration, documentation cleanup wave, financial algorithm, schema or default changes. New code must pass meaningful red/green and the stated controls; one integrated milestone and required external Claude review, then PR/CI/merge autonomously.

- **2026-10-29 — minimal implementation:** logical R commit `04ea879d`, integrated source candidate `5b5aa6a7`. Existing restart now clears `_real_viz_by_eq`, resets only each history object's `last_clearing_tick=-1` and resets `_real_consec_money_no_progress_ticks=0`. Owner/global refusal still precedes mutation; resume and sibling runs are untouched. Money, `committed_total`, `original_limit`, `clearing_count` and history identity survive. Consumer inventory: API→runtime→lifecycle restart; viz consumers API/manual, payment executor and clearing tick; history growth/decay and no-progress advance/confirmed-commit reset. No new epoch system, default change or active-restart race fix.
- **2026-10-29 — fresh real-path red/green:** new PostgreSQL selector before source fix:2failed6.30s/exit1. Exact first restarted tick: `actual=[16000, 16000], expected=[18000, 18000]; last refresh tick=101`; enabled-decay control `actual=1000.00000000, expected=900.00`. Normal91→92 cache hit→101 refresh and debt preservation passed before final failures. Original red test SHA256 `A1BB3FEB5062A1C02BC98B5B7C480F7A31DE684EA8DD3A40598104CCA0289E8B`, base owner blob `eac96ded75f399a3cbeb1fe5d52920ac7de55fce`; sources/logs/receipt in `.local-run/worktrees/p053-r/.local-run/p053-r-red-evidence/`. Counter-only mutation separately failed3 component cases (`assert 2 == 0`), restored before final verification. An earlier counter fixture setup failure is not product evidence.
- **2026-10-29 — acceptance and limits:** final developer canonical7-module cohort108passed17.75s/exit0/head037; integrated15-module canonical cohort184passed/1slow deselected98.68s/exit0/head037. Full selectors and integrated artifact owners are [recorded with the clearing follow-up](follow-up.md). PG tests exercise real planner/payment/tick and public runtime restart, holding heartbeat scheduling and stubbing storage side writes; this is runtime/DB acceptance, not HTTP/browser restart acceptance. Ordinary refresh10, decay0 and explicit decay0.1, owner/global refusal, resume, multiple equivalents and sibling isolation covered. Counter behavior invokes real policy as a component control, not a new DB-contention experiment. Pinned Ruff0.1.14/diff-check exit0; independent internal source review SOUND/class1=0/no findings, no execution claimed by reviewer. Active restart race remains accepted/unverified; integrated live100/restart and external result remain pending until their recorded completion.
- **2026-10-29 — completed milestone/review:** canonical `p053_live` exact realistic100+restart selector with `-IncludeExpensive`:2passed426.79s/exit0/head037, both scenarios100+25 ticks, preserved committed debt at restart,300+75 normal successes each,0 failures/no stalls/4+1 clearing events. Actual reports/four epochs inspected. Required Claude Code2.1.312 opus/high source review on frozen `7c9831da..5b5aa6a7`:VALID SOUND/class1=0/10unique checks, resolved `claude-opus-5-5`; no restart finding. One P3/class2 display-race candidate belongs to clearing, recorded in BACKLOG053-5 without implementation. Exact command/evidence/model/cost and review consolidation live in [clearing acceptance](follow-up.md). Source/tests unchanged after review; required CI/PR/merge remain delivery work for the main session.

- **2026-10-29 — delivery tracking:** [PR #363](https://github.com/slawa19/GEOv0/pull/363) owns the final commit, required CI job results and merge receipt. Local acceptance above is source acceptance; PR state is authoritative for delivery.
