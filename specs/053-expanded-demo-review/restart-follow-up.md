# 053 follow-up — Сброс привязанных к тактам кэшей при restart

- **Date:** 2026-10-29
- **Status:** IMPLEMENTATION AUTHORIZED — 2026-10-29; current owner instruction: «реализуй».
- **Status authority:** Source-method probe confirms the stale epoch mechanism; it is debug-only, not a live restart acceptance gate.
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
| R1 | Owner-selector inventory и meaningful red на stopped restart | [ ] authorized, pending execution |
| R2 | Минимальный сброс названных tick-bound полей, сохранение durable состояния | [ ] authorized, pending execution |
| R3 | Targeted verification, независимое review и соответствующий milestone | [ ] authorized, pending execution |

`[x]` done; `[ ]` in progress; `[!]` not authorized/blocked.

## Changelog

- **2026-10-29:** ARG-2 сужен по проверке: stale quantiles — основной подтверждённый эффект, decay/counter — условные последствия; неверный пример default demo отклонён. Проба и ограничения — в консолидации 053.

- **2026-10-29 — implementation authorization, before code:** owner «реализуй» starts F1–F3 and R1–R3. Root accepted the two existing specifications; independent owner surfaces may execute in parallel. No recorder regeneration, documentation cleanup wave, financial algorithm, schema or default changes. New code must pass meaningful red/green and the stated controls; one integrated milestone and required external Claude review, then PR/CI/merge autonomously.
