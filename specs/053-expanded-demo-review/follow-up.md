# 053 follow-up — Полный результат клиринга при закрытии линии

- **Date:** 2026-10-29
- **Status:** IMPLEMENTATION AUTHORIZED — 2026-10-29; current owner instruction: «реализуй».
- **Status authority:** The reproduced discrepancy below establishes the finding; this label does not establish a fix or passing acceptance.
- **Owner surface:** Proposed narrow changes in `app/core/simulator/edge_patch_builder.py` and its clearing callers, plus the named backend/UI behavioral tests. Exact caller list must be inventoried before implementation. No financial algorithm, schema, migration, generator or default-policy change.
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
| F1 | Red regression через реальную БД и настоящий UI consumer; инвентаризация всех clearing callers | [ ] authorized, pending execution |
| F2 | Минимальная post-commit публикация закрытых именно этим clearing линий | [ ] authorized, pending execution |
| F3 | Targeted tests, независимое review защищённого SSE slice, milestone и доставка | [ ] authorized, pending execution |

`[x]` completed; `[ ]` in progress; `[!]` not authorized/blocked. The review request authorized the specification; the separate owner instruction «реализуй» on 2026-10-29 authorizes this implementation wave.

## Changelog

- **2026-10-29:** root reproduced the ordinary signed-close/manual-clearing case on PostgreSQL, then both production UI event handlers. Logs and exact temporary test sources retained under `.local-run/expanded-review-20261029/`; findings consolidation remains in the parent review specification.

- **2026-10-29 — implementation authorization, before code:** owner «реализуй» starts F1–F3 and R1–R3. Root accepted the two existing specifications; independent owner surfaces may execute in parallel. No recorder regeneration, documentation cleanup wave, financial algorithm, schema or default changes. New code must pass meaningful red/green and the stated controls; one integrated milestone and required external Claude review, then PR/CI/merge autonomously.
