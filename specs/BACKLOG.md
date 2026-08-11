# BACKLOG — находки без программы

**Обновлено:** 2026-08-11

Здесь живут находки, которые не тянут на отдельную программу, но и не должны потеряться.
Правило AGENTS.md §2 прямое: узкая очевидная правка не требует церемонии; новый контракт или
межмодульная миграция — требует. Всё, что ниже, — первая категория либо ждёт продуктового решения.

Реестр программ и порядок работ: [`README.md`](README.md).

Статусы: `открыто` — подтверждено и не исправлено; `решение` — нужен выбор владельца, а не код;
`принято` — осознанно оставлено как есть.

## Требуют продуктового решения, не кода

| Пункт | Sev | Суть | Evidence |
|---|---|---|---|
| Admin: экран Events (timeline) | — | **Не фронтовая работа, но и не greenfield.** `GET /admin/events` действительно не существует (перечислены все 27 маршрутов `admin.py`, `/events` нет ни под каким именем; в `openapi.yaml` только `/simulator/events*`). Нет страницы, роута и пункта меню. **Нюанс:** `GET /admin/audit-log` (`admin.py:899`) покрывает лишь 10 операторских мутаций + логин и критерий «фильтр по `tx_id` даёт полный упорядоченный список шагов» удовлетворить не может. Зато `integrity_audit_log` **уже является процессным источником событий** с индексированным `tx_id`; работа сводится к «добавить фильтры + страницу», а не «построить пайплайн событий». Корреляции `run_id`/`scenario_id` нет нигде | нет маршрута: `app/api/v1/admin.py`; `admin-ui/src/router/index.ts` (12 записей — 10 страниц + редиректы `/` и `/feature-flags`; `/events` отсутствует); `admin-ui/src/layout/AppShell.vue:21-30`. Писатели аудита: `admin.py:276` `_add_audit_entry`, `auth.py:68`. Модель `app/db/models/audit_log.py:28-44` (`tx_id` index, `operation_type` PAYMENT/CLEARING/TRUSTLINE_*, checksum before/after, `affected_participants`, `error_details`); пишут `payments/engine.py:1134`, `clearing/service.py:1042`, `trustlines/service.py:122,226,319`, `simulator/real_tick_orchestrator.py:447`, `api/v1/integrity.py:266`. Чтение: `GET /integrity/audit-log` — `app/api/v1/integrity.py:470`, доступ `deps.require_participant_or_admin`, параметры **только** `page`/`per_page`, ноль фильтров; `admin-ui` его не вызывает (0 вхождений `integrity/audit-log` в `admin-ui/src`). Требование — `docs/ru/admin-ui/specs/UNFINISHED.md` п.1 |
| Admin: экраны Transactions / Clearing | — | **Не фронтовая работа, но объём меньше заявленного.** Списочных `GET /admin/transactions[/{tx_id}]` нет; есть только `POST /admin/transactions/{tx_id}/abort` (`admin.py:996`) и `GET /admin/clearing/cycles` (`:2056`). **Нюанс:** `GET /payments` и `GET /payments/{tx_id}` уже существуют с фильтрами `direction/status/equivalent/from_date/to_date/page/per_page` — они лишь заскоуплены на запрашивающего, и обхода для админа в коде нет. Задача = «снять requester-scoping за админским маршрутом», а не новая фича. **Поправка:** архивная рекомендация «использовать user API» неверна — у админского токена нет участника, к которому можно привязаться | `app/api/v1/payments.py:107` (список), `:91` (деталь); requester вшит в WHERE — `app/core/payments/service.py:1054-1070`, ветки `is_admin` нет. Неверная рекомендация — `docs/ru/archive/ui-spec-revision-proposal-2026-01-10.md:525-526`. Требование — `UNFINISHED.md` п.2 |
| Admin Liquidity Phase 2 (Bottlenecks edges, Participants net position, Concentration/HHI, Clearing impact) | — | Осознанно вне MVP. HHI/top-shares частично реализованы, но на странице Graph, а не как экран Liquidity | `admin-ui/src/composables/useGraphAnalytics.ts`, `operatorAdvice.ts:32-34,184-197`. Churn/Gini нет нигде |
| `audit.drift` не в принятом union нормализатора | P3 | Событие производится, но нормализатор относит его в `ignored('unknown')`. Переклассифицировано как продуктовое решение по наблюдаемости, а не доказанный дефект | основной производитель `app/core/simulator/real_tick_orchestrator.py:425`, второй — `app/core/simulator/real_payments_executor.py:152`; `simulator-ui/v2/src/api/normalizeSimulatorEvent.ts:568`; диагностический бакет `useSimulatorRealMode.ts:94` |
| `/simulator/events/poll` всегда возвращает `[]` | P3 | OpenAPI документирует массив событий; в коде комментарий «MVP: no replay buffer». Либо реализовать, либо убрать из контракта | `simulator.py:2407-2414`; `api/openapi.yaml:1013-1016` |
| Судьба `/ws` и `event_bus` | — | Маршрут живой, производитель есть, потребителя нет. Решение keep-or-deprecate заблокировано за F-005-1 (токен в query string) | `app/api/v1/websocket.py:17`; производитель — `event_bus.publish(` в `app/core/payments/service.py:94` |
| `docs/ru/pwa/` | — | Домена `pwa` нет в каноне `documentation-rules.md` §2.2, входящих ссылок нет. Мёртвый документ или отложенная работа | `docs/ru/pwa/specs/pwa-client-ui-spec.md` |

## Узкие правки — не требуют спеки

| Пункт | Sev | Суть | Evidence |
|---|---|---|---|
| Trustline timestamps — `[x]` 2026-08-11 | P2 на SQLite / P3 на PG | `TrustLine` теперь трактует потерявшие timezone SQLite timestamps как UTC и сохраняет явный aware offset. Pure schema и реальные create/get HTTP responses проверяют `created_at`/`updated_at`. Canonical `wave5_backlog_trustline_timestamps` — exit `0`, `32 passed`; финальный API selector `wave5_backlog_trustline_timestamps_api` — exit `0`, `4 passed`; pinned Ruff и diff-check — exit `0` | `app/schemas/trustline.py:22-30`; `tests/unit/test_trustline_timestamps.py`; `tests/integration/test_trustlines_get_by_id.py` |
| TODO-ESC — `[x]` 2026-08-11 | P3 | Anchor reconciliation показала, что finding уже исправлена коммитом `c3db303`: `WindowShell` предоставляет per-window container, destructive confirmation вешает/снимает listener только на нём, а тест доказывает, что container ESC disarm'ит, а global `window` ESC — нет. **Current = Intended = Optimal:** поведение не менять; удалены только stale TODO-labels. Targeted Vitest — exit `0`, `1 passed`; Simulator typecheck, diff-check — exit `0`; `rg TODO-ESC simulator-ui/v2/src` — exit `1`, ноль совпадений | `WindowShell.vue:45-46`; `useDestructiveConfirmation.ts:65-67,123-147`; `useDestructiveConfirmation.test.ts:45-99` |
| M20: `??` как молчаливый дефолт | P3 | 833 вхождения в двух фронтендах; действительно опасны **13 мест (22 физические строки)**. Сплошной codemod запрещён. Подробности и список — раздел «M20: разбор» ниже | см. раздел «M20: разбор» |
| Bottleneck-порог: float в SQL против decimal в mock | P2 | Бэкенд считает `bottlenecks` через `(available / limit) < float(threshold)` в SQL, mock — точным сравнением decimal-строк. На рёбрах ровно на пороге KPI расходится между режимами; это нарушает собственный критерий приёмки спеки «расчёты: детерминированные, decimal-safe (без float)». Плюс поле порога — свободный `el-input` без клиентской проверки, вне `[0,1]` реальный режим отвечает HTTP 422 | `app/api/v1/admin.py:648` (`threshold: float = Query(0.10, ge=0.0, le=1.0)`), `:668` (сравнение во float); тот же паттерн в `/admin/trustlines/bottlenecks` — `:553,596`. Mock: `admin-ui/src/api/mockApi.ts:1060-1062` → `utils/decimal.ts:104-129 isRatioBelowThreshold` (целочисленное сравнение `num * 10^s < den * thr`). Инпут — `admin-ui/src/pages/LiquidityPage.vue:245-250`. Требование — `docs/ru/admin-ui/specs/UNFINISHED.md` §3 |
| Непроверенные касты в API-клиентах | P3 | `simulatorApi.ts:192-313` — стёртые касты для действий симулятора (клиринг ингестится без валидации, `useInteractMode.ts:633-641`). Admin: списочные эндпоинты без Zod — `realApi.ts:663,751,778,796,871` | там же |
| 12 разошедшихся функций лаунчеров | P3 | Одноимённые функции с разными телами; `Stop-ProcessById` в трёх лаунчерах | `run_local.ps1:175`, `run_full_stack.ps1:280`, `run_real_simulator.ps1:151` |
| Дублирование политики в движке | P3 | `engine.prepare` и `engine.prepare_routes` дублируют ~240 строк политики вместимости/резервирования | `engine.py:359-580` против `:582-822` |
| Мёртвые экспорты | P3 | `router.find_paths` (алгоритм Йена) без runtime-вызова; `restartRun` без продуктового вызова; `bestEffortTotal` компенсирует уже устранённый пробел бэкенда | `router.py:552-630`; `simulatorApi.ts:127-131`; `realApi.ts:540-548` |
| [x] `tmp_*` скрипты под git | P3 | Закрыто 2026-08-11: четыре неиспользуемых tracked-скрипта удалены, мёртвая `Show-RecentLog` удалена из `run_full_stack.ps1`; канонические диагностики и fixture validators сохранены | До: `scripts/tmp_check_graph_isolates.js`, `scripts/tmp_sse_watch.py`, `scripts/fix_concatenated_admin_fixtures.py`, `scripts/verify_hybrid_approach.ps1`, `run_full_stack.ps1:378-402`. После: runtime/package/docs reference scan — только эта закрывающая запись и историческое evidence `specs/001-codebase-renovation/tasks.md:394`; `npm --prefix admin-ui run validate:fixtures` → exit `0`, `Fixtures OK`; `scripts/verify_local.ps1 -TaskSlug wave5_backlog_dead_scripts_cleanup -BackendOnly -BackendSelector tests/integration/test_simulator_sse_smoke.py,tests/integration/test_simulator_artifacts_events_ndjson.py,tests/unit/test_run_full_stack_database_url_redaction.py -Python ./.venv/Scripts/python.exe` → exit `0`, `120 passed` |
| Trust-drift мутация до коммита | P3 | Рост доверия меняет in-memory историю/сценарий до коммита. Поверхность trustlines, вынесена из 002 | `002/phase0-evidence-map.md:187` |
| 53 теста лаунчера молча пропускаются вне Windows | P3 | — | `002`/аудит, C-Risk#5 |

### Проглатывание исключений: экземпляры без владельца

Клиринговые «братья» этого паттерна принадлежат программе 003. Перечисленные ниже — **не принадлежат
никому**: поверхности `app/core/trustlines/` и `app/api/v1/integrity.py` не входят в owner surface
ни одной из программ 002–007. Регистрируются здесь, чтобы не потеряться до появления владельца.

**Два разных последствия, которые нельзя смешивать.** Почти во всех обсуждениях этого паттерна их
путают:

- **(а) отравление незакрытой транзакции.** Проглоченное исключение от запроса к БД оставляет
  сессию в сломанном состоянии, а код идёт дальше к `commit()`. Лечится транзакционной защитой
  (savepoint / явный rollback вложенной операции);
- **(б) потеря записи аудита при закоммиченном бизнес-изменении.** Проглоченное исключение
  отменяет вставку `IntegrityAuditLog`, но бизнес-мутация коммитится как ни в чём не бывало.
  Это молчаливый пробел **полноты аудита**, и транзакционная защита его **не чинит** — она лишь
  делает так, что запись гарантированно не пишется, а не пишется случайно.

| # | Место | Что именно проглатывается | Класс |
|---|---|---|---|
| 1 — `[x]` 2026-08-11 | `app/core/trustlines/service.py:75-78` | До правки `create()` проглатывал ошибку initial checkpoint до любой записи. Теперь failure пропагируется до `TrustLine`/commit; `tests/unit/test_trustline_audit_fail_closed.py:19-75` доказывает exception, `commit.assert_not_awaited()` и ноль строк после rollback. Canonical `wave5_backlog_trustline_precheckpoint` — exit `0`, `8 passed`; pinned Ruff и diff-check — exit `0` | (а), закрыто |
| 2 — `[x]` 2026-08-11 | `app/core/trustlines/service.py:106-135` | До правки голый `except Exception: pass` накрывал post-flush checkpoint и построение `IntegrityAuditLog`, после чего trustline коммитился без аудита. Теперь весь audit stage fail-closed до commit. Параметрический `tests/unit/test_trustline_audit_fail_closed.py:18-85` доказывает отказ и initial, и post-flush checkpoint: commit не вызван, после rollback строк нет. Canonical `wave5_backlog_trustline_create_audit` — exit `0`, `9 passed`; pinned Ruff и diff-check — exit `0` | (а) и (б), закрыто |
| 3 — `[x]` 2026-08-11 | `app/core/trustlines/service.py:168-250` | `update()` больше не проглатывает ни initial, ни post-flush checkpoint/audit failure: ошибка выходит до commit, rollback восстанавливает прежний limit. Первый `wave5_backlog_trustline_update_audit` честно завершился exit `1`, `2 failed, 8 passed`: test fixture создавал TrustLine до flush нового Equivalent и передавал `equivalent_id=None`. После исправления harness `wave5_backlog_trustline_update_audit_fix` — exit `0`, `10 passed`; pinned Ruff и diff-check — exit `0` | (а) и (б), закрыто |
| 4 — `[x]` 2026-08-11 | `app/core/trustlines/service.py:282-360` | `close()` теперь fail-closed на initial и post-flush checkpoint/audit stage. Параметрический `tests/unit/test_trustline_audit_fail_closed.py` проверяет обе точки: commit не вызван, rollback сохраняет `status='active'`. Canonical `wave5_backlog_trustline_close_audit` — exit `0`, `12 passed`; pinned Ruff и diff-check — exit `0` | (а) и (б), закрыто |
| 5 — `[x]` 2026-08-11 | `app/api/v1/integrity.py:252-258` | `POST /integrity/verify` больше не подменяет ошибку checkpoint старым/пустым checksum и не идёт к commit после возможно отравленного DB query. `tests/integration/test_integrity_endpoints.py` фиксирует forced checkpoint failure, `commit.assert_not_awaited()` и ноль audit rows после rollback. Canonical `wave5_backlog_integrity_checkpoint` — exit `0`, `17 passed`; pinned Ruff и diff-check — exit `0` | (а), закрыто |

**`integrity.py:284-285` — это НЕ тот паттерн, и внешнее ревью его переоценило.** Там `try`
накрывает только `db.add(IntegrityAuditLog(...))` и `model_dump()` — чисто in-memory операции,
никакого IO. Отравить транзакцию они не могут; максимум — скрыть ошибку сериализации. Держать в
одном списке с пунктами 1-5 неверно.

## M20: разбор

Перепроверено на HEAD `ea9cde9` (2026-08-10). Прежняя формулировка была неверна почти во всём.

**Масштаб.** `simulator-ui/v2/src` — **659 вхождений `??` в 628 строках 104 файлов**; `admin-ui/src` —
**174 вхождения в 160 строках 31 файла**. Итого **833**, а не 190. Утверждение «в admin-ui ноль
попаданий» ложно: `admin-ui/src/api/mockApi.ts` — 34 вхождения, `api/realApi.ts` — 27,
`composables/useGraphAnalytics.ts` — 17, `utils/decimal.ts` — 14.

**Почему старые цифры неверны.** Артефакты `plans/m20-nullish-coalescing-audit.{raw.txt,grouped.json,meta.json}`
внутренне согласованы (190 вхождений / 178 уникальных строк; формы `?? ''`=107, `?? 0`=61, `?? null`=22),
но не описывают кодовую базу по двум независимым причинам:

1. **Слепы к формам.** Регекс ловил только три литерала. Пропущены `?? []` (28), `?? false` (12),
   `?? {}` (8), `?? undefined` (8), `?? 1` (7), `?? true` (5) и ~211 дефолтов-идентификаторов/выражений.
2. **Устарели.** mtime артефактов — 2026-02-24, HEAD — 2026-08-10. Они покрывают **37 из 104** текущих
   файлов с `??` (38-й, `utils/escOverlayStack.test.ts`, из репозитория исчез). Оставшиеся **67 файлов
   держат 320 вхождений**; крупнейшие **среди нетестовых** — `composables/realEventPipeline.ts`
   (38; файл добавлен 2026-08-09), `composables/useWindowController.ts` (18),
   `composables/realFx/useRealTxFx.ts` (12), `composables/windowManager/useWindowManager.ts` (12),
   `components/NodeCardOverlay.vue` (10). Оговорка «нетестовых» существенна: вселенная 104/67 файлов
   тесты **включает**, и по абсолютному счёту выше половины этого списка стоят
   `components/SimulatorAppRoot.interact.test.ts` (28), `components/ManualPaymentPanel.test.ts` (22)
   и `components/TrustlineManagementPanel.test.ts` (13) — правки они не требуют, но ранжирование без
   этой оговорки вводит в заблуждение.

Guard'ов вида `String(… ?? '')` в simulator-ui — **96** (91 вне тестов), а не 67.

**Все восемь прежних «горячих точек» указывали не туда.** Пять смещены на 1-3 строки
(`TopBar.vue:69,95,96,102,103` → реально `?? 0` на `:72,98,99,105,106`; `SystemBalanceBar.vue:19` → `:20`),
три указывали на строки без `??` вообще (`useInteractDataCache.ts:222-223` — пустая строка и комментарий
`// ---`; `SimulatorAppRoot.vue:282` — `)`, `:474` — открывающая `computed<InteractPhase>`, `:622` —
`if (!snap) return null`).

**Флагманская находка «знаменатель success-rate в TopBar» дефектом не является.** `ctx.runStats` —
всегда материализованный реактивный объект (`useSimulatorApp.ts:518-527`, каждое поле инициализировано
`0`/`{}`), поэтому `?? 0` там — недостижимая мёртвая защита, а `successRatePct` и без того закрыт
`if (a <= 0) return 0`. То же с `SystemBalanceBar.vue:20`: `useSystemBalance.ts:50-56` возвращает полный
объект по умолчанию, `utilization` нулевым/undefined не бывает.

### Действительно требуют правки — 13 мест (22 строки)

| # | file:line | Что не так |
|---|---|---|
| 1 | `[x]` `simulator-ui/v2/src/composables/interact/useInteractDataCache.ts:118,438` | 2026-08-11: `normalizeAmount(unknown)` сохраняет trimmed исходную строку, если decimal-parser её отверг, и по-прежнему нормализует валидное значение. Контрпроверки обоих путей — `useInteractDataCache.snapshotTrustlines.test.ts:127-167`. Targeted Vitest — exit `0`, `3 passed`; Simulator typecheck — exit `0`; первый build остановился до компиляции на внешнем `DEBUG=release` (exit `1`, Pydantic bool parsing), повтор `DEBUG=false; npm --prefix simulator-ui/v2 run build` — exit `0` |
| 2 | `[x]` `…useInteractDataCache.ts:439` | 2026-08-11: snapshot `used` проходит через тот же `normalizeAmount`; невалидное непустое значение больше не превращается в `''`. Evidence и gates — пункт 1 |
| 3 | `[x]` `…useInteractDataCache.ts:441` | 2026-08-11: snapshot `available` проходит через тот же `normalizeAmount`; невалидное непустое значение больше не превращается в `''`. Evidence и gates — пункт 1 |
| 4 | `simulator-ui/v2/src/composables/useInteractMode.ts:638` | `const clearedCycles = res.cleared_cycles ?? 0` — отсутствующее поле неотличимо от честного нуля циклов |
| 5 | `…useInteractMode.ts:670` | `const settled = res?.cleared_cycles ?? 0` — «Clearing done: 0/0 cycles» на некорректном ответе |
| 6-8 | `admin-ui/src/pages/LiquidityPage.vue:139,140,141` | `active_trustlines / bottlenecks / incidents_over_sla ?? 0` в незащищённом `el-statistic` (блок KPI без `v-if` — `<el-row :gutter="12">` открывается на `:298`, блок `:298-317`) — при упавшей загрузке `summary` остаётся `null` и рядом с алертом об ошибке показывается жёсткий `0` |
| 9-11 | `admin-ui/src/pages/LiquidityPage.vue:149,150,151` | `String(total_limit / total_used / total_available ?? '0')` — «нет данных» превращается в денежный `0.00`. Точки отрисовки — `:value` на `:325`, `:332`, `:339` (не `:331`/`:338` — это `:title=`) |
| 12 | `admin-ui/src/composables/useGraphAnalytics.ts:464,465,469,470` (плюс `:176,247,279,382,454,480`) | **худший пункт набора**: `precisionByEq.value.get(eq) ?? 2` подаётся в `decimalToAtoms(value, prec)` (`:36`). Если список эквивалентов не загрузился или код разошёлся с нормализованным ключом карты (`useGraphData.ts:133` кладёт через `normalizeEqCode`, `useGraphAnalytics` ищет по сырому `eqCode`), эквивалент с precision ≠ 2 даёт порядок атомов, смещённый на 10^n, — молча портит агрегаты net/capacity |
| 13 | `admin-ui/src/pages/LiquidityPage.vue:146` | `precisionByEq.value.get(k) ?? 2` — тот же класс на стороне отображения; карта строится только из **активных** эквивалентов (`:103`, `listEquivalents({ include_inactive: false })`; `:108` — это уже вызов `liquiditySummary`), влияет на все денежные KPI |

На 2026-08-11 закрыт только независимый M20-срез 1–3; пункты 4–13 остаются открытыми и не входят
в evidence этого коммита.

### Не является дефектом — не поднимать заново

`TopBar.vue:72,98,99,105,106`; `SystemBalanceBar.vue:20,26`; `useInteractDataCache.ts:228,232`
(`String(v ?? '').trim()`); `SimulatorAppRoot.vue:623` (`snap.links ?? []`);
`EdgeDetailPopup.vue:127` — сделано намеренно: `:116` рисует `'—%'`, `:122` ставит aria «unknown»,
`?? 0` задаёт только ширину полосы; `api/simulatorContracts.ts:68` — месяц уже ограничен `1..12`
проверками рядом; `layout/forceLayout.ts:596` — `idxById` строится на `:243-244`, недостижимо;
все 96 guard'ов `String(x ?? '')`; все `?? []` перед циклами; все `?? null` как sentinel «ничего не выбрано».

### Вывод

Сплошной codemod по-прежнему запрещён, но по более сильной причине, чем раньше: 833 вхождения против
13 настоящих. И **самый тяжёлый пункт (`precisionByEq … ?? 2` → `decimalToAtoms`) находится в admin-ui,
который исходный аудит вообще не сканировал.**

Артефакты `plans/m20-nullish-coalescing-audit.*` следует **удалить, а не цитировать**: это устаревший
и слепой к формам снимок, он лежит в неотслеживаемом каталоге без git-истории, и 13 мест выше полностью
его замещают.

## Пробелы покрытия без владельца

Перечислены в [`006-verification-integrity/spec.md`](006-verification-integrity/spec.md) в разделе
«Пробелы покрытия без владельца»: `integrity.py`/`invariants.py` без независимой проверки, паритет
ORM↔миграции только для одной таблицы, ни одного браузерного прогона реального SSE, admin
real-transport smoke вне CI, конкурентность auth challenge/refresh помечена `UNVERIFIED / NO FIX`.

## Принято и остаётся как есть

Из остаточного реестра программы 001 (`001-codebase-renovation/phase7-closure-map.md:116-134`) —
проверено 2026-08-11, всё три пункта присутствуют в коде **по замыслу**:

| Пункт | Почему принято | Evidence |
|---|---|---|
| Неканонические equivalent-коды в сохранённых сценариях требуют ручного ремонта | Fail-closed по замыслу; новые и генерируемые сценарии используют канонические коды | `app/api/v1/admin.py:1131-1140` (`"reason": "noncanonical_code"`, `"repair": "manual_cleanup"`), `:1142-1153`; сидер `real_scenario_seeder.py:72-76` |
| Откат лаунчера восстанавливает состав запущенных сервисов, а не снимок образа/конфига | Принятое ограничение однонодовой dev-топологии | `run_local.ps1:485-533`, `run_real_simulator.ps1:779-796`, `run_full_stack.ps1:963`. Снимка образа не существует нигде |
| Канонический `ENV` выигрывает у legacy-алиаса `ENVIRONMENT` | Намеренное поведение; конфликтующие поддерживаемые значения по-прежнему fail-close | `app/config.py:87`, поле `:90-92`, резолвер `:258-283`, fail-close `:276-280` |
