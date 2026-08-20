# BACKLOG — находки без программы

**Обновлено:** 2026-08-12

Здесь живут находки, которые не тянут на отдельную программу, но и не должны потеряться.
Правило AGENTS.md §2 прямое: узкая очевидная правка не требует церемонии; новый контракт или
межмодульная миграция — требует. Всё, что ниже, — первая категория либо ждёт продуктового решения.

Реестр программ и порядок работ: [`README.md`](README.md).

Статусы: `открыто` — подтверждено и не исправлено; `решение` — нужен выбор владельца, а не код;
`спека` — работа оказалась шире узкой правки и требует отдельного owner surface/плана; `принято` —
осознанно оставлено как есть.

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

## Требуют отдельной спеки, не узкой правки

| Пункт | Sev | Current / Intended / Optimal | Evidence |
|---|---|---|---|
| Launcher runtime helpers — `спека` 2026-08-12 | P3 | **Current:** не «12 функций»: в трёх launcher-скриптах 15 повторяющихся имён, пять тел идентичны, десять различаются. `Stop-ProcessById` различается осознанной lifecycle-семантикой: full-stack требует fingerprint и ждёт подтверждения остановки, run-real принимает уже исчезнувший процесс, run-local допускает вызов без fingerprint. **Intended:** общие safety primitives имеют один проверяемый контракт, launcher-specific policy остаётся явной. **Optimal:** shared launcher-runtime module + migration/real start-status-stop milestones; это новый межскриптовый контракт и по AGENTS.md §2 не является узкой правкой. До спеки поведение не выравнивать копированием | AST inventory — exit `0`, `duplicates=15 identical=5 different=10`; идентичны `Exit-LauncherLifecycleLock`, `Get-EffectiveDatabaseUrl`, `Get-FullStackOwnershipMetadata`, `Get-LauncherLifecycleLockName`, `Get-ProcessIdentityObservation`; различаются `Assert-NoActiveFullStackOwnership`, `Enter-LauncherLifecycleLock`, `Get-ListeningPid`, `Get-ProcessStartTimeFingerprint`, `Get-ProjectTools`, `Invoke-PythonScript`, `Stop-ProcessById`, `Test-HttpEndpoint`, `Update-EnvLocal`, `Wait-ForPortToBeFree`. Anchors: `run_local.ps1:175`, `run_full_stack.ps1:280`, `run_real_simulator.ps1:151`; canonical launcher selector — exit `0`, `118 passed` |

## Узкие правки — не требуют спеки

| Пункт | Sev | Суть | Evidence |
|---|---|---|---|
| Trustline timestamps — `[x]` 2026-08-11 | P2 на SQLite / P3 на PG | `TrustLine` теперь трактует потерявшие timezone SQLite timestamps как UTC и сохраняет явный aware offset. Pure schema и реальные create/get HTTP responses проверяют `created_at`/`updated_at`. Canonical `wave5_backlog_trustline_timestamps` — exit `0`, `32 passed`; финальный API selector `wave5_backlog_trustline_timestamps_api` — exit `0`, `4 passed`; pinned Ruff и diff-check — exit `0` | `app/schemas/trustline.py:22-30`; `tests/unit/test_trustline_timestamps.py`; `tests/integration/test_trustlines_get_by_id.py` |
| TODO-ESC — `[x]` 2026-08-11 | P3 | Anchor reconciliation показала, что finding уже исправлена коммитом `c3db303`: `WindowShell` предоставляет per-window container, destructive confirmation вешает/снимает listener только на нём, а тест доказывает, что container ESC disarm'ит, а global `window` ESC — нет. **Current = Intended = Optimal:** поведение не менять; удалены только stale TODO-labels. Targeted Vitest — exit `0`, `1 passed`; Simulator typecheck, diff-check — exit `0`; `rg TODO-ESC simulator-ui/v2/src` — exit `1`, ноль совпадений | `WindowShell.vue:45-46`; `useDestructiveConfirmation.ts:65-67,123-147`; `useDestructiveConfirmation.test.ts:45-99` |
| M20: `??` как молчаливый дефолт — `[x]` 2026-08-12 | P3 | Исходные 13 мест закрыты независимыми срезами. Финальный precision-срез нормализует ключи, сохраняет precision `4` и fail-closed возвращает пустую/`null` аналитику без метаданных; Liquidity скрывает денежные итоги и показывает предупреждение вместо `?? 2`. Найденный вне исходного списка дубликат зарегистрирован отдельной строкой ниже | `useGraphAnalytics.ts:29-32,130-135,198-524`; `LiquidityPage.vue:133-155,214-218,312-330`; targeted Vitest — exit `0`, `26 passed`; Admin build — exit `0`; lint — exit `0`, `117` baseline warnings / `0` errors; diff-check — exit `0` |
| Graph histogram precision fallback | P3 | **Current:** runtime `GraphAnalyticsDrawer` и неиспользуемый `BalanceTab` всё ещё форматируют атомы через `precisionByEq.get(eq) ?? 2`. **Intended:** отсутствие precision не должно менять порядок величины. **Optimal:** общий fail-closed renderer/prop после подтверждения owner surface; не включено молча в M20-срез composable/Liquidity | `admin-ui/src/pages/graph/GraphAnalyticsDrawer.vue:815-816`; `admin-ui/src/pages/graph/tabs/BalanceTab.vue:71-72` |
| Participant timestamps без UTC-normalization | P3 | **Current:** trustline/admin-audit/incident schemas уже нормализуют naive SQLite timestamp как UTC, а public/admin participant DTO возвращают `created_at`/`updated_at` без такого validator. **Intended:** wire timestamps однозначно timezone-aware. **Optimal:** отдельный schema/API slice с SQLite countercheck; не смешан с закрытым trustline timestamp finding | `app/schemas/participant.py:27,33-34`; `app/schemas/admin.py:108-117`; сравнить `app/schemas/admin.py:90-95,140-147` |
| Bottleneck-порог: float в SQL против decimal в mock — `[x]` 2026-08-12 | P2 | **Current:** три real endpoint-а сравнивали Numeric через float/SQL, mock — decimal-safe, а три UI-поверхности отправляли свободную строку. **Intended:** строгий `< threshold`, одинаковый на точной границе и для high-precision decimal; `[0,1]` проверяется до запроса. **Optimal:** общий backend Decimal predicate + общий UI parser/guard, без изменения wire schema. Реализовано в `02feee7` | Backend loader/predicate: `app/api/v1/admin.py:96,567,669-694,793-810,2175`, `app/core/admin/metrics.py:61-72,516-531`; transport/UI guards: `realApi.ts:577-590,705,715,943`, Liquidity `:44,101-107,257,265`, Dashboard `:30,111-117,169-172,493,499`, Graph `useGraphAnalytics.ts:140-145` + toolbar `:111,347`. Первый backend gate exit `4` до collection из-за inherited `DEBUG=release`; с `DEBUG=false` — exit `0`, `31 passed`. Первый Admin build exit `1` (mock tuple и shadowed `t`), после исправления — exit `0`; full Admin test — `219 passed`; lint — exit `0`, `117` baseline warnings / `0` errors; pinned Ruff `0.1.14` и diff-check — exit `0` |
| Непроверенные касты в API-клиентах — `[x]` 2026-08-12 | P3 | Simulator: девять action/list/target 2xx shapes проходят `simulatorContractJson` (`simulatorApi.ts:197-362`, decoders `simulatorContracts.ts:637-681`). Admin: общий Zod pagination wrapper требует `items/page/per_page/total`, а equivalents list проверяет item schema (`realApi.ts:291-306,674-893`). Обе стороны fail-closed до composables. Внешний review нашёл P2 в первой версии Admin schema: canonical nullable audit actor/object, trustline policy, equivalent description и incident created_at ошибочно требовали non-null строки/объект. Remediation синхронизировала Zod с backend schemas и оставила numeric/type anti-vacuum для каждого nullable поля | Simulator RED — exit `1`, `9 failed / 23 passed`; green contract + downstream — exit `0`, `44 passed`; временный overly-narrow snapshot type дал typecheck exit `1` (2 ошибки), после разделения strict backend decoder/optional snapshot fallback typecheck, build и lint — exit `0`. Admin RED — exit `1`, `5 failed / 5 passed`: все malformed 2xx принимались; green focused — exit `0`, `20 passed`; full Admin — exit `0`, `229 passed`; build — exit `0`; lint — exit `0`, `117` baseline warnings / `0` errors. Review remediation `realApi.ts:111-172`, `realApi.listContracts.test.ts:30-140`: targeted exit `0`, `31 passed`; build и diff-check — exit `0` |
| Дублирование политики в движке — `[x]` 2026-08-11 | P3 | **Current:** `prepare` и `prepare_routes` независимо повторяли SQL/JSON-расчёт capacity и persisted reservations. **Intended:** single- и multipath используют одну формулу, сохраняя разные validation, lock aggregation, `local_reserved`, retry и logging. **Optimal:** общий приватный helper в том же модуле; lifecycle-пути не объединяются. Реализовано и закрыто | До: `app/core/payments/engine.py:571-793,795-1036`; после: общий `_get_segment_capacity_and_reserved_usage` на `:571-651`, entrypoints `:653,818`, call-sites `:754,933`; anti-vacuum/equivalence `tests/integration/test_payment_prepare_capacity_policy.py:122-204`. Canonical non-PG `wave5_backlog_prepare_policy_nonpg` → exit `0`, `64 passed`; `wave5_backlog_prepare_policy_2pc` → exit `0`, `7 passed`; disposable PG `geov0_test_wave5_prepare_policy_helper_811`, `-BackendMarker postgres`, три concurrency-selector → exit `0`, `16 passed`; pre-create absent, pre-drop connections `0`, post-drop absent. Pinned Ruff `0.1.14` и diff-check → exit `0`; Black `24.1.1` нового теста → exit `0`, существующий `engine.py` diagnostic → exit `1`, `would reformat engine.py` (repository-wide baseline debt не расширен) |
| Мёртвые экспорты — `[x]` 2026-08-12 | P3 | Неиспользуемые `PaymentRouter.find_paths`/Yen и UI `restartRun` удалены после нулевого runtime-reference scan; policy/max-hop проверки перенесены на живой `find_flow_routes` (`router.py:460-550`, `test_routing_reserved_and_policy.py:177-256`). `bestEffortTotal` удалён только после подключения обязательного pagination schema — его fallback больше не может маскировать malformed backend 2xx | До: `router.py:552-630`, `simulatorApi.ts:127-131`, `realApi.ts:541-549`; после: active-tree reference scan по `restartRun|find_paths|bestEffortTotal|heapq` — `0`. Первый backend запуск exit `2` из-за nonexistent selector; исправленный — exit `0`, `7 passed`. Simulator contract — `14 passed`, typecheck/build — exit `0`. Удаление pagination fallback доказано Admin RED/green и full gates из соседнего закрытого пункта |
| [x] `tmp_*` скрипты под git | P3 | Закрыто 2026-08-11: четыре неиспользуемых tracked-скрипта удалены, мёртвая `Show-RecentLog` удалена из `run_full_stack.ps1`; канонические диагностики и fixture validators сохранены | До: `scripts/tmp_check_graph_isolates.js`, `scripts/tmp_sse_watch.py`, `scripts/fix_concatenated_admin_fixtures.py`, `scripts/verify_hybrid_approach.ps1`, `run_full_stack.ps1:378-402`. После: runtime/package/docs reference scan — только эта закрывающая запись и историческое evidence `specs/001-codebase-renovation/tasks.md:394`; `npm --prefix admin-ui run validate:fixtures` → exit `0`, `Fixtures OK`; `scripts/verify_local.ps1 -TaskSlug wave5_backlog_dead_scripts_cleanup -BackendOnly -BackendSelector tests/integration/test_simulator_sse_smoke.py,tests/integration/test_simulator_artifacts_events_ndjson.py,tests/unit/test_run_full_stack_database_url_redaction.py -Python ./.venv/Scripts/python.exe` → exit `0`, `120 passed` |
| Trust-drift мутация до коммита — `[x]` 2026-08-11 | P3 | **Current:** growth менял scenario/cache до собственного commit, decay — до commit внешнего tick-owner; при rollback БД и runtime расходились. История clearing уже описывает ранее подтверждённый clearing и остаётся немедленной. **Intended:** limit/cache публикуются только при подтверждённом commit. **Optimal:** staged `TrustDriftLimitUpdate` и общий post-commit applicator, без нового слоя транзакций | До: `trust_drift_engine.py:253-282,405-431`; после: staged result `models.py:32-44`, applicator `trust_drift_engine.py:104-134`, growth commit resolution `:314-329`, decay owner callback `real_tick_trust_drift_coordinator.py:83-97`. RED после staging — exit `1`, `2 failed / 52 passed` (`1000 != 980.0`, `350 != 300.0`); targeted green — exit `0`, `57 passed`; extended sibling matrix — exit `0`, `75 passed`; failure/cancellation anti-vacuum `test_trust_drift.py:381-446`, `test_real_tick_commit_cancellation.py:341-404`; pinned Ruff `0.1.14` и diff-check — exit `0` |
| 53 теста лаунчера молча пропускаются вне Windows — `[x]` 2026-08-12 | P3 | **Current:** формулировка устарела: тесты не Windows-gated, а ищут `pwsh`/`powershell` на любой ОС; при отсутствии интерпретатора skip явный (`PowerShell is required`). В файле 51 logical test: 39 требуют PowerShell, 12 source-policy тестов независимы; параметризация двумя доступными интерпретаторами даёт 118 cases. **Intended:** required Windows CI и локальный canonical gate действительно выполняют PowerShell cases. **Optimal:** product/test code не менять; отдельный Linux portability job был бы изменением CI policy, а не узкой правкой. Canonical `wave5_launcher_tests_status` — exit `0`, `118 passed`; AST inventory — exit `0`, `logical_tests=51 powershell_dependent=39 source_only=12` | `tests/unit/test_run_full_stack_database_url_redaction.py:16-47,107-108`; `.github/workflows/quality.yml` required Windows job |

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

**2026-08-12 / remediation внешнего ревью.** Первоначальные тесты пунктов 1–4 подменяли весь
checkpoint-helper и не проверяли его внутреннюю границу. Ревью exact HEAD `92e86a6` обнаружило, что
`compute_integrity_checkpoint_for_equivalent()` проглатывал неожиданный отказ `InvariantChecker`,
возвращал checkpoint без `passed`, а потребители трактовали отсутствие как `True`. Теперь только
ожидаемый `IntegrityViolationException` записывается как проверенный failed-check; недоступный checker
пропагируется владельцу UoW, а отсутствующий `passed` fail-closed означает `False` во всех потребителях
checkpoint. Новые counterchecks ломают именно `InvariantChecker.check_zero_sum` и доказывают exception,
отсутствие commit и отсутствие TrustLine. Canonical `wave5_extrem_integrity` — exit `0`, `25 passed`;
pinned Ruff `0.1.14` и scoped diff-check — exit `0`.

Повторное ревью нашло соседний batch false-green: `compute_and_store_integrity_checkpoints()` всё
ещё проглатывал тот же отказ, коммитил пустой batch и позволял background supervisor опубликовать
`*_success`. Batch теперь откатывается и повторно выбрасывает любой `BaseException`; существующий
supervisor переводит job в `failed/*_error`. Countercheck с настоящим inner checker доказывает
исключение и terminal rollback. Canonical `wave5_extrem_integrity_batch2` — exit `0`, `31 passed`;
pinned Ruff и diff-check — exit `0`.

**`integrity.py:284-285` — это НЕ тот паттерн, и внешнее ревью его переоценило.** Там `try`
накрывает только `db.add(IntegrityAuditLog(...))` и `model_dump()` — чисто in-memory операции,
никакого IO. Отравить транзакцию они не могут; максимум — скрыть ошибку сериализации. Держать в
одном списке с пунктами 1-5 неверно.


**Пополнение 2026-08-20 — `T809-B1`, класс (а) в самом денежном ядре.**
`app/core/payments/engine.py:538-541`: отказ `await self.session.rollback()` накрыт
`except Exception: pass`, после чего цикл **продолжает ретрай на сессии в неизвестном состоянии**.
Это буквально механизм, вокруг которого построена закрытая программа 004: проглоченный отказ
отравляет транзакцию, а следующий отказ уже не попадает в retry-предикат. Дедупликация выполнена —
якорь `engine.py:53x` в `specs/` не встречался.

Знаменатель класса предъявлен впервые: **82 голых `except` в `app/`, из них 46 (56 %) в денежном и
recovery-ядре** (`payments/engine.py` — 20, `payments/service.py` — 15). Число является **нижней
границей**: `contextlib.suppress` и формы с комментарием между строками не считались.

**Владелец правки — follow-up программа, а не 008** (решение оркестратора 2026-08-20, владелец
согласился). Денежное ядро в программе 008 объявлено read-only именно затем, чтобы ревью не рождало
правок в самом рискованном коде без собственной спеки и гейтов; §5 `codex-orchestrator-rule.md`
требует для несвязанной находки явно принятого follow-up, а не расширения текущей фазы.
Контрдовод Codex записан и остаётся в силе: при этом маршруте живой риск целостности стоит дольше.
**Исполнение в любом случае ждёт подъёма Postgres** — правка `payments/engine.py` требует
Postgres-гейта с одноразовой БД по матрице `plan.md` §6, а на текущей машине БД не поднята.
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
| 4 | `[x]` `simulator-ui/v2/src/composables/useInteractMode.ts:638` | 2026-08-11: `actionClearingReal` теперь декодирует 2xx через `simulatorContracts.ts:382-411,432-434`; обязательный целый `cleared_cycles >= 0`, canonical `from`/`to` и остальная форма проверяются до передачи в interact mode, поэтому ложный `?? 0` удалён. Красный прогон contract-test до decoder — exit `1`, `3 failed / 11 passed`, `expected ... to be an instance of SimulatorContractError`; после исправления contract + downstream interact selectors — exit `0`, `38 passed`; typecheck и build (`DEBUG=false`) — exit `0` |
| 5 | `[x]` `…useInteractMode.ts:670-671` | 2026-08-11: финальный status использует обязательные `res.cleared_cycles` и `res.cycles.length`; отсутствующее/строковое поле и неканонический edge отклоняются, а честный ноль проходит (`simulatorApi.contract.test.ts:146-150,203-233`). Evidence и gates — пункт 4 |
| 6-8 | `[x]` `admin-ui/src/pages/LiquidityPage.vue:139-141,299-322` | 2026-08-11: при отсутствующем `summary` счётчики теперь остаются `undefined`, а KPI-row не монтируется; честные серверные нули после успешной загрузки сохраняются. Countercheck — `adminAsyncOwnership.test.ts:341-372`. Первый targeted run был exit `1`, `1 failed / 13 passed`: shallow-stub `ElStatistic` не рендерил дочернее значение; после исправления теста — exit `0`, `14 passed`. |
| 9-11 | `[x]` `admin-ui/src/pages/LiquidityPage.vue:149-151,325-351` | 2026-08-11: при отсутствующем `summary` денежные computed возвращают `null`, а весь KPI-row скрыт; строковый серверный `"0"` остаётся честным нулём. Первый build был exit `1`: advice-контракт получил optional computed; после чтения полей из подтверждённого `summary` build (`DEBUG=false`) — exit `0`, targeted Vitest — exit `0`, `14 passed`, `git diff --check` — exit `0`. |
| 12 | `[x]` `admin-ui/src/composables/useGraphAnalytics.ts:29-32,130-135,198-524` | 2026-08-12: все decimal→atoms пути используют нормализованный ключ и только подтверждённый non-negative integer precision; при отсутствии metadata derived analytics возвращает `[]`/`null`, а не атомы precision=2. Тесты доказывают mixed-case `EUR`, precision `4` (`0.0001` = один атом) и fail-closed missing-map |
| 13 | `[x]` `admin-ui/src/pages/LiquidityPage.vue:133-155,214-218,312-330` | 2026-08-12: precision `0` больше не превращается в `2`; lookup нормализован. Без выбранного/загруженного equivalent денежный KPI-row скрыт, таблицы показывают `—`, UI выводит явное предупреждение; строки trustline форматируются по собственному equivalent. Gates: targeted Vitest exit `0`, `26 passed`; build exit `0`; lint exit `0`, `117` baseline warnings / `0` errors; diff-check exit `0` |

На 2026-08-12 все 13 исходных M20-пунктов закрыты независимыми срезами 1–3, 4–5, 6–11 и 12–13.
Найденный при финальном reference scan sibling в histogram renderer зарегистрирован отдельной открытой
строкой реестра и не выдаётся за часть исходного набора.

**2026-08-12 / correction внешнего ревью для пункта 13.** Коммит `976c391` поставил precision-guard
на строку немонетарных count KPI и оставил денежные total limit/used/available видимыми при
`selectedPrecision=null`, то есть первоначальная запись выше была ложноположительной. Guard перенесён
на денежную строку; count KPI остаются видимыми при загруженном summary. Countercheck фиксирует обе
ветки через `showCountKpis`/`showMoneyKpis`. Targeted Admin Vitest — exit `0`, `28 passed`; Admin build
— exit `0`; scoped diff-check — exit `0`.

Повторное ревью показало, что первый remediation-test смотрел только exposed computed и оставался бы
зелёным при обратной перестановке template bindings. Тест теперь рендерит default slot root-card и
проверяет оба `data-testid`: count-row остаётся, money-row исчезает без precision. Targeted Vitest —
exit `0`, `17 passed`; перестановка guard'ов больше не может пройти вхолостую.

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

## Принятые риски — датированные решения владельца

### 2026-08-14 — внешнее ревью запускается при достижимом credential

**Решение владельца:** риск принят, внешнее ревью Codex запускается на этой машине как есть.
Основание в правилах — `AGENTS.md` §15: «владелец явно и датированно принимает риск с записью
в `specs/BACKLOG.md`». Право снять требование есть только у владельца, и оно применено осознанно.

**Что именно принято.** `AGENTS.md` §15 требует, чтобы внешний ревьюер не видел credential
в достижимой файловой системе. На этой машине условие **не выполняется и не может быть выполнено
средствами CLI**:

- `codex exec --sandbox read-only` ограничивает **запись**, но не **чтение**. Проверено эмпирически
  2026-08-14 отдельным пробным прогоном (`PROBE-DONE: A=READABLE B=READABLE C=READABLE`, exit 0):
  ревьюер читает `C:\Users\slawa\.git-credentials` и `C:\Users\slawa\.codex`;
- настройки, **сужающей** область чтения до workspace, в `codex-cli 0.147.0-alpha.6.6` нет —
  существует только расширяющая (`sandbox_permissions=["disk-full-read-access"]`);
- файл credential живой: 68 байт, одна строка, изменён 2026-08-10.

**Следствие для дисциплины evidence.** В ledger любого прогона на этой машине строка «credential-free»
писаться **не должна**. Пишется: «credential достижим, риск принят владельцем 2026-08-14, ссылка
на эту запись». Разница существенна: первое — выполненное условие, второе — принятый риск.

**Что это НЕ разрешает.** Решение не распространяется на другие машины, на прогоны с переносом
исходников и на направление «Codex как оркестратор». Не отменяет требований §15 о замороженном
`<BASE>..<HEAD>`, read-only режиме, проверяемом завершении и записи модели.

**Как закрыть по-настоящему, если понадобится:** отдельный прогон в окружении без credential
(контейнер либо машина без git-credential store), с записанным SHA переносимого набора.

### 2026-08-14 — неверная запись credential-free в ledger волны 2 (исправлено)

Ledger промежуточного внешнего ревью волн 1–2 (`specs/008-surface-code-review/evidence-index.md`)
утверждал «Credential-free: проверено… `.git-credentials`, `.netrc`, `.env` отсутствуют». Проверка
смотрела **пути внутри репозитория**, тогда как credential store лежит в домашнем каталоге. То есть
прогон волны 2 фактически шёл с достижимым credential, а запись об этом была неверной. Утверждение
исправлено 2026-08-14 в самом ledger. Это запись о **дефекте проверки**, а не о новом риске: сам риск
принят выше.

## Принято и остаётся как есть

Из остаточного реестра программы 001 (`001-codebase-renovation/phase7-closure-map.md:116-134`) —
проверено 2026-08-11, всё три пункта присутствуют в коде **по замыслу**:

| Пункт | Почему принято | Evidence |
|---|---|---|
| Неканонические equivalent-коды в сохранённых сценариях требуют ручного ремонта | Fail-closed по замыслу; новые и генерируемые сценарии используют канонические коды | `app/api/v1/admin.py:1131-1140` (`"reason": "noncanonical_code"`, `"repair": "manual_cleanup"`), `:1142-1153`; сидер `real_scenario_seeder.py:72-76` |
| Откат лаунчера восстанавливает состав запущенных сервисов, а не снимок образа/конфига | Принятое ограничение однонодовой dev-топологии | `run_local.ps1:485-533`, `run_real_simulator.ps1:779-796`, `run_full_stack.ps1:963`. Снимка образа не существует нигде |
| Канонический `ENV` выигрывает у legacy-алиаса `ENVIRONMENT` | Намеренное поведение; конфликтующие поддерживаемые значения по-прежнему fail-close | `app/config.py:87`, поле `:90-92`, резолвер `:258-283`, fail-close `:276-280` |
