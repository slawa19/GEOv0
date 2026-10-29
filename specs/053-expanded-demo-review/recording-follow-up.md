# 053 follow-up — Согласовать перезапись плейлистов с их проверками

- **Date:** 2026-10-29
- **Status:** SPECIFIED — implementation not started; before the next intentional playlist regeneration.
- **Status authority:** Fresh PostgreSQL recipe results demonstrate the mismatch below; the full recorder SSE transport was not rerun in this review.
- **Owner surface:** `scripts/record_workbench_playlists.py`, `tests/integration/test_p050_workbench_recording_recipe_postgres.py`, `simulator-ui/v2/src/composables/usePlaylistWorkbench.test.ts`, `simulator-ui/v2/e2e/p042-t4205-playlist-workbench.spec.ts`; generated public assets only as an explicit regeneration with review. No planner, financial algorithm or scenario tuning.
- **Origin:** ARG-3, root verification on frozen dc7a9054; reviewer P2, root disposition P3/class 2, conditional tooling/fixture maintenance. Current recorded demo is unaffected; perform before the next intentional regeneration. Review authorizes this specification, not new implementation.

## Problem

050 обновила рецепт записи под новую сеть, но проверки записанных файлов удерживают конкретные значения старого плейлиста. Текущие публичные плейлисты не менялись и этим замечанием не объявляются сломанными. Проблема возникает при следующей штатной перезаписи по инструкции.

## Findings

`usePlaylistWorkbench.test.ts:86` ожидает8 рёбер; :291 —3 цикла и1 общее ребро; `p042-t4205-playlist-workbench.spec.ts:157–159` —8 рёбер и247.95 UAH. Свежий canonical `test_p050_workbench_recording_recipe_postgres.py` (20 реальных HTTP-платежей и clearing, изолированная PostgreSQL): 1passed10.41s, exit0; фактический ответ — **4 цикла,11 уникальных рёбер,3 общих ребра,175.00 UAH**. Тест проверяет жизнеспособность рецепта, но не его совместимость с этими UI assertions.

Argon предсказал7 или9 рёбер и2 или3 цикла; эти числа **опровергнуты** свежим исполнением. Общая несовместимость подтверждена, точный состав новой записи заранее константами не навязывается. Root не выполнял `record()` с реальным SSE-транспортом и не перезаписывал public fixtures.

## Current / Intended / Optimal

Current: актуальный рецепт исполняется, его результат расходится с hardcoded checks прежней записи. Intended: воспроизводимая осознанная перезапись проходит финансовый валидатор и показывает именно записанный результат. Optimal: сначала получить полный новый комплект snapshot+events штатным recorder, затем согласовать тесты с продуктовым инвариантом и явными требованиями выбранной записи; сохранить независимые денежные проверки и anti-vacuum. Измерения не подгоняются под старые8/3/247.95, assertions не ослабляются до «что-то произошло».

## Non-goals

Не менять текущую сеть посетителя или принимаемые economic thresholds; не добавлять generalized recorder framework; не регенерировать assets попутно с исправлением клиринга или restart.

## Verification plan

- Red: полный новый комплект, записанный через `record()` на реальном backend, проходит stream/snapshot money reconciliation, но выявляет несовпадение прежних exact-fixture assertions. Проверять стадийно, не лечить ошибку подменой ожидаемых чисел из ответа API.
- Сохранить инварианты:20 committed payments с нужными маршрутами, непустое пересечение циклов, реальное соответствие node/edge patches и долгов, отсутствие unpatched money, schema/normalizer acceptance. Существующие mutation counterchecks в `usePlaylistWorkbench.test.ts` обязаны остаться содержательными.
- Canonical recipe selector: `scripts/verify_local.ps1 -TaskSlug p053_recording_fix -BackendOnly -BackendSelector tests/integration/test_p050_workbench_recording_recipe_postgres.py`; UI `npm --prefix simulator-ui/v2 run test:unit -- src/composables/usePlaylistWorkbench.test.ts`; затем точный Playwright selector `e2e/p042-t4205-playlist-workbench.spec.ts` и необходимые UI gates по AGENTS.
- Запрещено: заменять независимый финансовый validator подсчётом полей, оставлять canned-stream test единственным evidence реального recorder, ослаблять assertions только для зелёного прогона, выдавать current fixture success за new-recording success.

## Tasks

| ID | Task | Status |
|---|---|---|
| W1 | Записать новый полный комплект вне public, проверить деньги и несовместимые assertions | [!] implementation not started |
| W2 | Согласовать recipe/tests/assets одной осознанной регенерацией с diff review | [!] implementation not started |
| W3 | Unit/targeted live/Playwright checks, независимое review | [!] implementation not started |

`[x]` done; `[ ]` in progress; `[!]` not authorized/blocked.

## Changelog

- **2026-10-29:** root выполнил существующий canonical рецепт, сохранил response и сравнение actual/expected в `.local-run/expanded-review-20261029/{recipe-probe.log,recipe-observation.json,recipe-comparison.json}`. Числовые предсказания Argon исправлены; текущие публичные записи не изменены.
