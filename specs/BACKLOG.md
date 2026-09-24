# BACKLOG — находки без программы

**Обновлено:** 2026-09-21

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
| ~~`/simulator/events/poll` всегда возвращает `[]`~~ — **закрыт 2026-08-23 программой 011** | P3 | Развилка вынесена владельцу и решена делегированием внешнему ревьюеру (`VERDICT-F0117: A`): контракт описывает то, что есть. Оба документа объявляют массив с `maxItems: 0`, а описания `equivalent`/`after` больше не обещают семантику курсора, которой в MVP нет. Реализация replay-буфера остаётся отдельной продуктовой функцией и **не** является долгом этой строки. Прежние якоря этой строки к моменту закрытия оба указывали не туда — обработчик уехал на `simulator.py:2583`, а `openapi.yaml:1013-1016` попал внутрь другого пути | `app/api/v1/simulator.py` (`# MVP: no replay buffer.`); `api/openapi.yaml` (`/simulator/events/poll`); `specs/011-canon-describes-what-it-returns/spec.md`, `F-011-7` |
| Судьба `/ws` и `event_bus` | — | Маршрут живой, производитель есть, потребителя нет. Решение keep-or-deprecate заблокировано за F-005-1 (токен в query string) | `app/api/v1/websocket.py:17`; производитель — `event_bus.publish(` в `app/core/payments/service.py:94` |
| `docs/ru/pwa/` | — | Домена `pwa` нет в каноне `documentation-rules.md` §2.2, входящих ссылок нет. Мёртвый документ или отложенная работа | `docs/ru/pwa/specs/pwa-client-ui-spec.md` |
| npm-уязвимости в lock-файлах обоих UI — найдено 2026-09-20 | — | **Замер:** `admin-ui` — 20 (1 low, 4 moderate, 14 high, 1 critical), из них в production-зависимостях 4 high; `simulator-ui/v2` — 9 (1 moderate, 6 high, 2 critical), в production 2 high. **Дешёвого пути нет, и это главное в записи:** `npm audit fix` без `--force` трогает 28 и 18 пакетов соответственно и не закрывает ни одной записи — итоговые числа после него те же. Всё остальное требует `--force`, то есть мажорных апгрейдов build-тулчейна, а он же является механизмом, которым репозиторий себя проверяет (§5): апгрейд до рефакторинга рискует гейтом, на котором рефакторинг измеряется, после — копит долг. Поэтому это выбор момента владельцем, а не узкая правка. **Чем это НЕ является:** не состоянием установки — дерево чистое, `package-lock.json` не трогался; не связано с `audit.drift`, `integrity_audit_log` и каталогами `_audit*` | `npm --prefix admin-ui audit`, `npm --prefix simulator-ui/v2 audit`; production-числа — те же команды с `--omit=dev`; `npm audit fix --dry-run` → `removed 2 packages, changed 28 packages` (admin-ui) и `changed 18 packages` (simulator) при неизменных итоговых числах. Общие для обоих деревьев advisory — `GHSA-c2c7-rcm5-vvqj` (picomatch ReDoS), в admin-ui дополнительно `GHSA-xxjr-mmjv-4gpg` (lodash) |

### Схемная гигиена: `trustlines.policy` — nullable-колонка, в которую никто не пишет `null`

Внесено 2026-08-23 при закрытии 011. Колонка объявлена nullable и имеет **только Python-side
`default=`** (`app/db/models/trustline.py:15-21`), который срабатывает лишь когда атрибут не задан;
server default отсутствует. При этом **ни один писатель приложения не пишет `null`**: `create`
кладёт `data.policy or {}` (`app/core/trustlines/service.py:190`), `update` кладёт словарь
(`:334-336`), симуляторное действие не передаёт kwarg и получает дефолт ORM
(`app/api/v1/simulator.py:1116-1121`), инжектор и real-mode-сидер передают словари
(`inject_executor.py:506`, `:622`; `real_scenario_seeder.py:213-224`), `scripts/seed_db.py:359-382`
приводит не-словарь к `{}`.

Разрыв нашёлся так: два юнит-теста вставляли `policy=None` **прямо через ORM**, минуя
Python-дефолт, и строили строку, которую приложение написать не может — из-за чего проверка
конформности ответов канону видела `policy: null` на проволоке. Фикстуры исправлены на `{}`
(`tests/unit/test_admin_liquidity_summary.py`, `tests/unit/test_admin_trustlines_bottlenecks.py`),
и канон **сознательно** продолжает утверждать, что `policy` — объект: объявить его nullable значило
бы ослабить верное утверждение ради строки, которой служба не производит.

Остаток — не контрактный дефект, а гигиена схемы: либо `nullable=False` + server default, либо
явное решение, что `null` допустим, и тогда канон обязан это сказать. Владельца нет.

### Индекс артефактов симулятора обещает content type, которого выгрузка не отдаёт

Внесено 2026-08-24 при закрытии 011. `artifact_content_type` (`app/core/simulator/helpers.py:8`)
возвращает `application/json`, `application/x-ndjson`, `application/zip` — и вызывается **только**
из `list_artifacts` (`app/core/simulator/artifacts.py:153`) и `sync_artifacts`
(`app/core/simulator/storage.py:158`). Сама выгрузка
(`GET /simulator/runs/{run_id}/artifacts/{name}`, `app/api/v1/simulator.py:2938`) отдаёт
`FileResponse(path)` **без** `media_type`, поэтому тип на проволоке — догадка `mimetypes`.

Измерено: `events.ndjson` приезжает как `text/plain`, тогда как индекс объявляет для того же файла
`application/x-ndjson`. `bundle.zip` зависит от машины: `application/x-zip-compressed` под Windows
(реестр), `application/zip` в других окружениях — поэтому канон вынужден объявлять оба.

Канон описывает то, что **отдаёт выгрузка**, и это правильно для 011. Разрыв между индексом и
выгрузкой — поведенческий: закрыть его значит передать `media_type=artifact_content_type(name)` в
`FileResponse`, а это меняет заголовок, который получает клиент, и запрещено `## Non-goals` 011.
Владельца нет.

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

| `.snap` не покрыт `.gitattributes` — `[x]` 2026-09-20, найдено 2026-08-21 | P3 | **Current (было):** `.gitattributes` перечислял `*.ts`, `*.vue`, `*.json` и прочее как `eol=lf`, но `*.snap` не назван и попадал под `* text=auto`; vitest переписывал снапшот с LF, поэтому обычный прогон оставлял рабочее дерево грязным без единого изменения содержимого (`git status` показывает файл изменённым, `git diff` пуст). **Intended:** зелёный прогон не меняет рабочее дерево. **Optimal — шире, чем записанная здесь одна строка.** Замер 2026-09-20 показал, что `.snap` — один из **35** файлов, лежащих в дереве как CRLF при `i/lf` в индексе: туда же `*.txt`, `*.mjs`, `*.cjs`, `*.toml`, `*.ini`, `*.mako`, `.nvmrc`, `Dockerfile`, `.gitignore`. Правило `*.snap text eol=lf` закрыло бы один литерал из класса, поэтому вместо него `* text=auto eol=lf`: решение про LF перестаёт зависеть от `core.autocrlf` конкретного клона, а Windows-native скрипты держат CRLF собственными `eol=crlf`-строками. Расхождение с прежним «Optimal» записано здесь, а не обойдено молча (AGENTS.md §1) | `.gitattributes` (первая строка + комментарий с причиной); `simulator-ui/v2/src/legacyReference/__snapshots__/legacyWindowsMarkupSnapshots.test.ts.snap`. Замер до правки: `git ls-files --eol` → `1364 i/lf w/lf`, `44 i/lf w/crlf`, из них 13 — `ps1/cmd/bat`. Воспроизведение до правки: два полных прогона `verify_local.ps1` подряд, оба оставили снапшот `M`. После правки и ренормализации дерева — прогон гейта дерево не пачкает |
| Декодирование вывода subprocess по локали хоста — найдено 2026-09-20 | P3 | **Current:** семь тестовых call-site'ов зовут `subprocess.run(..., text=True)` без `encoding=`, то есть декодируют вывод дочернего процесса кодовой страницей локали машины. Этот класс уже выстрелил в восьмом месте: на ru-RU pwsh 7.6.6 локализованный префикс `WARNING` приходит в UTF-8, `cp1252` не знает байта `0x9D`, reader-поток умирает, `stdout` приходит `None`, и 13 тестов падают `TypeError`, не дойдя до своей диагностики. Восьмое место закрыто коммитом `aa2353b`; оставшиеся семь запускают `sys.executable` или `bash`, поэтому сегодня молчат — но по удаче (у этих детей вывод сейчас ASCII), а не по устройству. **Intended:** результат гейта не зависит от языка интерфейса и кодовой страницы машины, на которой он запущен. **Optimal:** `encoding="utf-8", errors="replace"` на каждом из семи вызовов — класс clean, продуктового выбора нет. Не включено в срез, который чинил восьмое место: это был бы попутный рефакторинг семи чужих модулей (AGENTS.md §9), а по §19.5 находка относится к классу 2 — свойство механизма проверки, не потеря на денежном пути. **Чего CI не увидит:** `windows-latest` en-US печатает ASCII-`WARNING`, поэтому required-гейт зелёный независимо от того, исправлено это или нет | `tests/migrated_schema.py:90`, `tests/contract/test_p011_responses_conform_to_the_canon.py:1502`, `tests/integration/test_p015_step5c_hold_races_postgres.py:599`, `tests/unit/test_alembic_postgres_only.py:30,59`, `tests/unit/test_deployment_config.py:156`, `tests/unit/test_p015_b4_entries_and_money.py:890`, `tests/unit/test_postgres_marker_fail_closed.py:23`. Закрытое восьмое место для сравнения — `tests/unit/test_run_full_stack_database_url_redaction.py:105,1388`. Воспроизведение класса: хост с `$PSUICulture` не en-US и `locale.getpreferredencoding(False)` не UTF-8 |

### Класс 2 из закрывающего ревью программы 015 (`T1509`) — внесено 2026-09-21

Обе находки вернул Codex на замороженном `056e720..d2aa411`, обе `VERDICT-CONFIRMED`, обе перепроверены оркестратором
по коду. `CLASS-1-COUNT: 0`, поэтому по §19.5 программу они не держат.

**Получатель обеих — владелец, диспозиционно.** §19.5 требует дату *и* получателя, и запись «получателя нет»
требование не удовлетворяет, а лишь обнажает пропуск (найдено консультацией Codex 2026-09-21). Программы,
владеющей этими поверхностями, действительно не существует: 015 закрыта, 016 не авторизована, а путь совместимости
SQLite и `docs/ru/02-protocol-spec.md` не входят в owner surface ни одной живой программы. Поэтому получателем
назначен владелец — и назначение означает **решение о судьбе**, а не авторизацию на реализацию (§19.5: рефакторинг
без наблюдаемой потери не авторизуется).

**Предполагаемый исполнитель, записан 2026-09-21 по указанию сессии, заведшей черновики 017–022.** Получателем по
§19.5 остаётся **владелец** — ссылка ниже его не заменяет, потому что программы 017–022 существуют только как
**неавторизованные черновики** в ветке `claude/017-core-refactoring-specs` (`57b1169`) и в `main` их нет; указатель
на них не может быть получателем, пока владелец их не вычитал. Сопоставление сделано по их `## Owner surface`, а не
по теме: **017** (Postgres единственным движком) владеет `app/config.py`, `app/db/sqlite_transaction_control.py` и
`app/main.py:323-500` — то есть тем самым путём совместимости SQLite, и при реализации он этот путь **удаляет**, а
вместе с ним и находки, которые на нём стоят; **019** (платёж одной транзакцией) переписывает
`app/core/payments/service.py`, где живут обе находки про гонку вставки. Где соответствия по owner surface нет —
`scripts/seed_db.py`, `docs/ru/02-protocol-spec.md`, красный scheduled-job, — исполнитель **не назван**, и
догадка вместо него не поставлена.

| Пункт | Sev | Суть | Evidence |
|---|---|---|---|
| SQLite-совместимость добавляет колонку удержания без внешнего ключа | P3 | Модель объявляет `ON DELETE RESTRICT` «на обоих диалектах» — доказательство удержания нельзя удалить, пока удержание на него ссылается. Путь совместимости для **существующего SQLite-файла** добавляет только nullable-колонку, **намеренно и с записанной причиной**: FK ссылается на `debt_reconciliation_results`, которой в файле старше шага 5a нет, и при `foreign_keys=ON` SQLite отказал бы в записи вовсе. Следствие: на таком файле удаление строки-доказательства ничем не отвергается. **Деньги при этом не проходят:** отказ читает саму колонку, а не FK, поэтому удержание переживает удаление доказательства — теряется причина, аудит и возможность объяснить, почему эквивалент стоит. PostgreSQL получает FK миграцией 028, свежий SQLite — из `create_all`; дыра только у dev-файлов, созданных до шага 5a | объявление — `app/db/models/equivalent.py:26`; пропуск с причиной — `app/main.py:377`, ALTER — `:408`; отказ читает колонку — `app/core/payments/engine.py:474-484`, `app/core/payments/service.py:742-749` |
| Протокол в RU-дереве говорит, что сверки нет, а она есть | P3 | `docs/ru/02-protocol-spec.md` утверждает: «Расхождение между `debts` и журналом операций… Это **не сделано** и принадлежит программе 015. До тех пор состояние честное: проверки нет, и система об этом говорит». К `d2aa411` это неверно — сверка реализована и планируется. Это ровно тот случай §1, когда документ расходится с кодом, и опаснее обычного: нормативное дерево уверяет читателя, что обнаружения **нет**, тогда как именно на его наличии стоит условие остановки §19.5. Правка узкая и не требует спеки, но по §19.5 она **не становится задачей закрываемой программы** | `docs/ru/02-protocol-spec.md:1802`; реализация — `app/core/ledger/reconciliation.py:780` (`verify_journal_equals_change`); планировщик — `app/main.py:176` |


### Долг CI без владельца: `Simulator visual E2E` красный минимум с 2026-09-14 — внесено 2026-09-21

Найдено при проверке CI после пуша закрытия 015. **Запись существует потому, что провалившаяся проверка не может
называться «косметикой» без разрешения владельца (§18), а нигде в репозитории она не зафиксирована.** Получатель —
владелец, диспозиционно: 007 закрыта, программы, владеющей этой поверхностью, нет.

| Пункт | Sev | Суть | Evidence |
|---|---|---|---|
| `Simulator visual E2E on baseline platform (scheduled/manual)` падает на расписании | — | Job запускается **только** по расписанию и вручную и **не блокирует** PR (`AGENTS.md` §5), поэтому краснота не видна в обычной работе. Падают **одни и те же два сценария** trustline из 24; остальные семь job'ов расписания, включая `PostgreSQL integration` и `container-smoke`, зелёные. **Причина одна и та же на обеих датах — это установлено совпадением подписи отказа, а не совпадением имени job'а:** те же два локатора, те же числа. Чего установить не удалось: отчего сценарии перестали проходить; на прогоне 2026-09-21 лог дополнительно показывает `Error: connect ECONNREFUSED 127.0.0.1:18000` от dev-бекенда, к которому проксирует UI, но причина это или симптом — **не установлено** | прогон `35557881677`, 2026-09-21 03:32, `schedule`, SHA `9fb4e77`: `2 failed`, `22 passed (44.8s)`, ожидание `locator('[data-testid="tl-limit-too-low"]')` и `locator('[data-testid="trustline-close-btn"]')`, `Process completed with exit code 1`. Прогон `34802959906`, 2026-09-14 03:32, SHA `aa74acf`: те же два локатора, `2 failed`, `22 passed (48.0s)`. Последний зелёный на расписании — `34079938994`, 2026-09-07, SHA `d1866fe`. Команда проверки: `gh run list --workflow Quality --event schedule` |


### Класс 2 из среза `T1548` (программа 015, шаг F2) — внесено 2026-09-20

Найдено агентом при реализации `T1548` и не чинилось там по §19.5: ни одна из четырёх не является потерей
на денежном пути. **Получатель — владелец, диспозиционно** (проставлено 2026-09-21 по тому же основанию, что и
у раздела выше): программы, владеющей этими поверхностями, в `main` нет. **Предполагаемый исполнитель** — см. оговорку
в разделе выше: staged-гонка и зависимость ответа от окружения стоят в `app/core/payments/service.py`, который
переписывает черновик **019**; невозможность мерить гонку вставки на SQLite-тире и падение юнит-тестов вне своего тира
исчезают вместе с SQLite, то есть относятся к черновику **017**. Шестая, `test_payment_timeouts.py`, — тоже
**019**, но по другому основанию: она проверяет тайм-аут над механизмом, который 019 удаляет целиком (долговечный
`PREPARED`, жнец, два тайм-аута), то есть по правилу тестового актива той же ветки это корзина **D** — тест уходит
вместе с механизмом, а не чинится на месте. Для `scripts/seed_db.py` соответствия нет. Якоря первой и третьей перепроверены оркестратором по коду; поведенческие утверждения
первой и второй записаны как измерение агента и **оркестратором независимо не воспроизведены**.

| Пункт | Sev | Суть | Evidence |
|---|---|---|---|
| Staged-ветка гонки вставки не доходит до собственного обработчика | P3 | **Current:** в режиме `commit=False` INSERT идёт внутри `async with self.session.begin_nested()`; когда он поднимает `IntegrityError`, повторный `select(...)` в `except`-блоке падает `PendingRollbackError`, а если вызывающий обернул вызов в собственный `begin_nested()` — `InvalidRequestError`. То есть `_resolve_existing_payment` на staged-гонке не вызывается вовсе, и исполнитель тика записывает `INTERNAL_ERROR` вместо классифицированного конфликта. Денег это неправильно не двигает и долга не теряет — поэтому класс 2, а не 1. **Дефект предшествует `T1548`.** Измерено однососессионной конструкцией на SQLite; поведение под настоящей конкуренцией PostgreSQL — **не установлено** | `app/core/payments/service.py:939-946` (повторный поиск в обработчике), вызывающий с собственным `begin_nested()` — `app/core/simulator/real_payments_executor.py:421` **Повышено 2026-09-21 срезом `T1523`: воспроизведено на настоящем PostgreSQL двумя по-настоящему конкурентными соединениями** — второе соединение коммитит конфликтующую строку между поиском и вставкой, и наружу вместо объявленного `409` выходит `sqlalchemy.exc.InvalidRequestError: Can't operate on closed transaction inside context manager`. То есть прежняя оговорка «под конкуренцией PostgreSQL не установлено» снята. **Чего у этой записи нет:** проба была одноразовой и в дереве не сохранена, поэтому утверждение **не открывается командой** — при следующем заходе его придётся воспроизводить заново, и это само по себе слабость записи (§15). Денег по-прежнему не теряет: дублирующий staged-платёж рапортует внутренней ошибкой вместо конфликта. |
| Ответ на гонку вставки зависит от окружения, а не от случая | P3 | Одна и та же гонка на SERIALIZABLE даёт клиенту **разный совет**: при `23505` повторное чтение отвечает «Payment with same tx_id is in progress» (не retryable, «ждите»), при `40001` классификатор отвечает retryable-конфликтом «отправьте снова». Внесено 2026-09-21 ячейкой 5 матрицы `T1523`: в прогоне одного модуля стенд попадал в первую ветку, в полном PostgreSQL-тире — во вторую, и первый полный прогон на этом и покраснел, потому что премиса предполагала уникальное нарушение. Денег ни одна ветка не двигает дважды, поэтому класс 2. **Почему ветка переключается — не установлено;** гипотеза, записанная в самом тесте: процесс-глобальный кэш графа `PaymentRouter` — промах кэша заставляет проигравшего прочитать строки, которые победитель затем пишет, и SSI отказывает раньше, чем консультируется уникальный индекс | ветки — `app/core/payments/service.py:930-946` (re-read `23505`) против `:947-995` (классификация `40001`); тест фиксирует фактическую ветку и ассертит именно её — `tests/integration/test_p015_t1523_in_progress_and_insert_race_postgres.py:373` |
| `test_payment_timeouts.py` подделывает собственный коммит | P3 | Тест правит состояние прямым `UPDATE transactions SET state='COMMITTED'` (`:186-194`) вместо настоящего коммита движка, то есть проверяет тайм-аут над эффектом, которого не было. Ячейка 7 матрицы `T1523` перекрывает его настоящим коммитом (`tests/unit/test_p015_t1523_the_commit_landed_then_the_caller_failed.py:183,239`), поэтому покрытие не потеряно. Старый тест **не тронут намеренно**: бриф `T1523` запрещает довешивать ассерты на соседние тесты. Он не мёртв — краснеет под той же мутацией, что и ячейка 7, — просто слабее, чем читается | `tests/unit/test_payment_timeouts.py:112,186-194` |
| SQLite-тир не может измерять идемпотентность гонки вставки | P3 | Два настоящих соединения до ветки `UNIQUE(tx_id)` на SQLite не доходят: WAL-снимок читателя превращает проигравший INSERT в `SQLITE_BUSY`, который классифицируется в `RetryablePaymentConflictException` раньше, чем нарушение уникальности вообще рассматривается. Не дефект, а **граница измерителя**: заявлять по SQLite-тиру, что гонка вставки проверена, нельзя | `app/core/payments/service.py:109-133` (`_classify_payment_db_error`) |
| `scripts/seed_db.py` пишет `PAYMENT`-строки без отпечатка | P3 | После `T1548` это единственный в дереве производитель строк, повтор которых приложение теперь отказывается обслуживать. Сегодня безвредно: повтор их `tx_id` недостижим — ключи сидированных участников не проходят `VerifyKey(..., Base64Encoder)`, а два беcподписных пути минтят собственные ключи. Значение записи — предупредить того, кто когда-нибудь переиспользует сидер | `scripts/seed_db.py:515-527`, `:543-562` (литерал `"idempotency": None` на `:555`); 240 таких строк в двух v2-паках; проверка подписи — `app/core/auth/crypto.py:16`, отказ до поиска идемпотентности — `app/core/payments/service.py:707` против `:723-731` |
| Юнит-тесты SQLite-тира падают, если их направить на PostgreSQL | P3 | Диагностическое, предшествует срезу: с `TEST_DATABASE_URL` на PostgreSQL тесты, исполняющие настоящий платёж, падают `InvalidRequestError` в коммите. Контрольный прогон **нетронутого** `tests/unit/test_p1_payment_run_perimeter.py` в той же конфигурации падает так же — то есть это свойство фикстуры вне своего тира, а не правки. Запись нужна, чтобы такой прогон не приняли за регрессию | контроль: `tests/unit/test_p1_payment_run_perimeter.py` (6 из 12 падают вне тира) |


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

**Обновление 2026-08-21 — замер сделан воспроизводимым, запись подтверждена.** Программа 010 заведена
([`010-money-core-fail-closed/spec.md`](010-money-core-fail-closed/spec.md)); знаменатель теперь
пересчитывается скриптом
[`measure_swallowed_exceptions.py`](010-money-core-fail-closed/measure_swallowed_exceptions.py) по AST.
Определение здешнего числа — «тело обработчика есть ровно `pass`» — воспроизвелось: доля **56%**
совпала точно, `payments/engine.py` дал те же **20**. На HEAD `24e03d1` числа выросли до **90 / 51**
за счёт срезов T714/T715, то есть «нижняя граница» была честной формулировкой.
`contextlib.suppress` в `app/` — **0 вхождений**, истинно голых `except:` — **0**.

**Что замер изменил по существу:** класс градуирован, и сплошной правки в нём нет. Из шести сайтов
денежного ядра, где проглочен отказ `rollback()`/`commit()`, ручной разбор оставил **один** дефект
(`payments/engine.py:540`); `clearing/service.py:57` пробрасывает, `:143` инвалидирует соединение
fail-closed и является образцом правильной формы, `recovery.py:245`/`:250` возвращают `False`, а
`clearing/service.py:219` продолжает на **отдельной** сессии и переведён во вход в разбор, а не в
дефект. Якорь самой находки уточнён: `:537-541`, а не `:538-541`.

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

## Долги волны 011 — поведенческие, вне её Non-goals

Внесены 2026-08-23 при закрытии программы 011. Каждый из трёх найден внутри волны, ни один не может
быть в ней исправлен: у 011 в `## Non-goals` стоит «не менять поведение приложения», а все три
требуют именно этого. Владельца нет ни у одного.

| Долг | В чём он | Почему не в 011 | Якоря |
|---|---|---|---|
| `F-011-8` | Политика trust-линии валидируется на публичном пути (`validate_trustline_policy` на `POST`/`PATCH /trustlines`) и **не** валидируется на симуляторном: сидер сценариев пишет в ту же колонку произвольный JSON, и эти строки отдаются теми же ответами | Закрытие требует либо валидации на втором пути, либо санации хранимых строк — и то и другое меняет поведение | `app/utils/validation.py`, `api/openapi.yaml` (`TrustLine.policy`, `additionalProperties: true` намеренно) |
| `F-011-9`, поведенческая часть | Одна и та же временная метка уходит в двух форматах: через модель — с `Z`, через `list[Any]` графового ответа — без смещения. Три поля: `AdminGraphTransactionItem.created_at/updated_at` и `incidents[].created_at` | Починка добавит смещение в строку на проволоке. Решение внешнего ревьюера `VERDICT-F0119: DESCRIBE_ONLY`; **в 012 не передаётся** — 012 владеет денежным представлением, а не временем | `app/api/v1/admin.py:217`, `:252-253`; `app/schemas/graph.py:32` |
| Разрыв generated-схемы SSE | Ни одна из четырёх схем SSE-событий не попадает в `app.openapi()`: эти модели нигде не служат `response_model`, а SSE-маршруты возвращают `StreamingResponse`. Клиент, сгенерированный из приложения, не знает **всего семейства событий**, а не отдельных полей | Требует либо публикации схем через отдельный механизм, либо изменения способа отдачи потока | Установлено `RT-011-5`; защищённый §8 контракт SSE существует только в `api/openapi.yaml` |

Кандидат-получатель для всех трёх — 013 либо отдельная узкая спека. Ни один из них не «мелкая
правка»: первые два меняют то, что видит клиент, третий меняет способ публикации контракта.

## Долг без владельца после волны 012 — форматирование денег на выходе симуляторного API

Внесено 2026-08-24 при заведении программы 015. Запись регистрирует **разрыв владения**, а не новую
находку: сама находка принадлежит внутреннему adversarial-ревью программы 012 и записана ею в
`cfa4475`. Здесь она значится потому, что после закрытия 012 у неё не останется получателя.

**Это два разных долга, и разводить их обязательно** — поправка владельца 012 от 2026-08-24. Слитые в один пункт, они заставят следующего читателя искать экспоненциальную запись там, где её нет.

| Долг | В чём он | Почему ни у кого нет | Якоря |
|---|---|---|---|
| **1. Форматтеры не читают точность эквивалента** | Девятнадцать производств денежной строки через два локальных форматтера, ни один из которых не читает `Equivalent.precision`. Число проверено грепом и исполнением: `_fmt_decimal_for_api` — 16 вызовов плюс определение, `_fmt_num_or_str` — 4. Спека 012 называла 18, её ревьюер 22; оба числа считали разные множества | 012 держит этот файл **только на валидацию входа** и прямо записала «recorded, not fixed: that is output formatting». 015 файл в owner surface не берёт: его находки лежат в `payments/router.py`, `api/v1/integrity.py` и трёх файлах `core/simulator/`. Все прежние владельцы — 009, 010, 011 — закрыты | `app/api/v1/simulator.py:784` (`_fmt_decimal_for_api`), `:2112` (`_fmt_num_or_str`) |
| **2. Написание клиента уезжает в снапшот дословно** | С `T1201` дверь принимает `"0.100000000"` — величина `0.1`, хранится точно, — поэтому снапшот сценария может нести написание, которого в леджере нет. **Экспоненциальная запись здесь недостижима**, и это проверено: `limit` в этих трёх местах объявлен `str \| None`, все вызывающие передают поля, типизированные `str`, `Decimal` до них не доходит, а `str()` над строкой — тождество | Тот же разрыв владения, что и у пункта 1 | `app/api/v1/simulator.py:867`, `:885`, `:894` |

Первичная запись обеих — внутреннее adversarial-ревью программы 012 (`T1210`), в её спеке и в коммите `cfa4475`.

Кандидат-получатель — 013 либо узкая спека вместе с `F-015-9` (форма денег на выходе публичных
маршрутов), с которой это один класс. **Прецедент, ради которого запись и заведена:** программа 012
уже передавала форму денег на `GET /trustlines` в программу 011 записью «это `F-011-1`, поверхность
011»; 011 закрылась, не взяв класс, и находка не всплыла нигде до сплошного ревью 2026-08-24. Передача
в закрывающуюся программу равна потере, поэтому здесь зафиксирован не адресат, а факт его отсутствия.

## Долги без владельца после фикс-раунда `T1211` волны 012

Внесено 2026-08-25. Все четыре найдены при закрытии находок внешнего ревью `T1211`, **измерены**, и
ни одна не относится к предмету 012 (деньги: precision, scale и представление). Прежние владельцы
поверхностей закрыты, поэтому здесь регистрируется не адресат, а факт его отсутствия — по тому же
правилу, что и раздел выше.

### 1. Примеры в документации не сверяются со схемами ничем

**Замерено, а не выведено из отсутствия находок.** `api/openapi.yaml` читают **16** файлов в
`tests/` — и все до одного сверяют со схемой **код**, ни один не открывает Markdown. Единственный
тест, который вообще читает документацию, — `tests/unit/test_deployment_config.py:190-215`: он пиннит
строки команд docker-compose в `06-contributing.md`. Ссылки на `02-protocol-spec.md` в тестах
(`test_p1_trustline_reopen_postgres.py:8,144`, `test_interact_actions_backend_p1.py:1642,1684`) —
прозаические цитаты в комментариях, не валидация. Ни `.github/workflows/quality.yml`, ни
`scripts/verify_local.ps1` документацию не касаются; pre-commit, Makefile, nox и tox в репозитории
отсутствуют.

**Что это стоило:** `"limit": 1000.00` в протокольных спеках трёх языков давал `422` с декабря 2025
(когда `api/openapi.yaml` объявил `limit: string`) и был замечен только сплошным ревью 2026-08-25.
Исправлен в `2790e28`.

**Почему не закрыто здесь.** Закрыть — значит разметить, какой fenced-блок какой схеме принадлежит, а
эта же задача показала, что по содержимому это **не выводится**: большинство блоков в
`02-protocol-spec.md` сознательно не соответствуют схемам двери (внутренние записи транзакций,
hub→участник сообщения, конверт `ERROR`). Извлечение json-блоков с прогоном через pydantic-модели
двери реализуемо, но требует явной разметки. Это отдельная узкая спека, а не правка.

### 2. RU §5.2 неполон, и перевод здесь полнее источника

Необычное направление дрейфа, поэтому записано отдельно:

- `docs/ru/02-protocol-spec.md:357` (источник) — «(Hub v0.1) `limit` ≥ 0»;
- `docs/en/02-protocol-spec.md:367` и `docs/pl/02-protocol-spec.md:353` (перевод) — «New `limit` ≥
  current `debt[to→from]`».

**Оба утверждения истинны, и каждое называет свою половину правила.** Код обеспечивает и то и другое:
`parse_money_amount(..., require_non_negative=True)` (`app/core/trustlines/service.py:340`) и отдельно
`if new_limit < used: raise BadRequestException("Cannot reduce trustline limit below used amount")`
(`:364-369`), где `used` определён как «debt where debtor is `to` and creditor is `from`»
(`:759-775`) — то есть буквально `debt[to→from]` из EN.

Поэтому приводить EN/PL к RU было бы **вредно**: это стёрло бы верное знание. Дописать порог в RU
внутри волны 012 нельзя — источник вне её предмета, и правка источника документации требует
владельца. Долг: RU не называет порог `limit ≥ used`, который дверь реально проверяет.

Смежно и той же природы (замечено, не чинилось): EN/PL §5.1 шаг 5 говорят «Create
`TRUST_LINE_CREATE (COMMITTED)` transaction», где RU говорит «Зафиксировать запись в
`IntegrityAuditLog` (best-effort)»; EN/PL §6.2 не содержат поля `tx_id` и абзаца про идемпотентность,
которые в RU есть. Датированный дрейф перевода.

### 3. Пример конверта `ERROR` §9.5 расходится с рантаймом

`docs/{ru,en,pl}/02-protocol-spec.md` §9.5 показывает `details: {limit: 1000.00, requested: …}`
числами. Схема здесь **ничего не требует**: `details` во всех схемах ошибок `openapi.yaml` — свободный
`type: object` без объявленных ключей, поэтому под правило «правь только то, где схема говорит
`string`» этот сайт не попал и в `2790e28` не тронут.

Но рантайм в этом месте отдаёт **строки**: `details={"used": str(used), "limit": data.limit}`
(`app/core/trustlines/service.py:367-368`). То есть пример расходится с поведением по причине, не
связанной с денежным контрактом, и решать надо не «строка или число», а объявлять ли `details`
типизированно вообще.

### 4. Покрытие real mode симулятора: один тест ничего не проверяет, один вотчер не покрыт ничем

Оба вскрыты правкой харнесса `useSimulatorRealMode.test.ts` (`createRealState()` возвращал обычный
объект там, где прод оборачивает состояние в `reactive`, — `useSimulatorApp.ts:546`), и оба **не
исправлены**: дефекта продакшен-кода в них нет, это долг покрытия, а предмет 012 — деньги.

| Долг | Измерение |
|---|---|
| Тест `stale run context (runId changed) prevents pending debounce timer from triggering loadScene()` **не проверяет гард из своего названия**. Его debounce-колбэк не исполняется ни разу: таймер снимает `stopSse() → cancelPendingRefreshSnapshotDebounce()` из SSE-context вотчера. Ассерт держится совершенно другим механизмом | Обезвреживание `if (real.runId !== runIdAtStart) return false` (`useSimulatorRealMode.ts:442`) оставляет **34 passed** — и на новом харнессе, и на старом |
| **Scenario-change вотчер** (`useSimulatorRealMode.ts:1402`) не покрыт ничем | Полное обезвреживание обработчика оставляет **34 passed**. Проверено оркестратором независимо от исполнителя 2026-08-25 |

Правка харнесса ни один из 34 существующих тестов утверждающим **не сделала** — это записано прямо,
чтобы «харнесс починен, 34 зелёных» не читалось как выросшее покрытие. Её ценность в другом: харнесс
перестал быть структурно неспособным дёргать `real.*`-вотчеры (находка 6 ревью `T1211` в нём была
непредставима), и вскрылись эти два долга.

## Долги без владельца после closure-среза программы 007

Внесено 2026-08-25 closure-срезом ledger'а 007. Программа 007 закрыта по реализации 2026-08-21;
её ledger был устаревшим. Из шести пунктов долгов `T716`/`T717` три оказались уже закрытыми —
`T716(а)` программой 011 (`T1105`, коммит `f44ae24`), `T717(а)` программой 009 (`T902`), `T717(в)`
задачами `T700`/`T701` самой 007. Три остались живыми. Ни один из трёх файлов не входит в owner
surface открытых программ реестра — проверено по таблице `README.md` построчно: 013 — оба
фронтенда и `admin.py`; 014 — `tests/**`, гарды, `quality.yml`, `invariants.py`; 015 берёт из
`core/simulator/` только `inject_executor.py`, `trust_drift_engine.py` и `real_payment_planner.py`;
016 — integrity/audit/error evidence, Admin AuditLog и извлечение ошибок симулятора. Единственная
формально подходящая — 012 («денежное представление в `app/core/simulator/`»), но она в закрытии, а
прецедент раздела «Долг без владельца после волны 012» прямо гласит: передача в закрывающуюся
программу равна потере. Поэтому здесь регистрируется не адресат, а факт его отсутствия.

Полные формулировки, чем опровергнуты прежние, и якоря — в строках `T716`/`T717`
[`007-simulator-analytics-surface/spec.md`](007-simulator-analytics-surface/spec.md).

### 1. Переполнение `NUMERIC(20,8)` на `total_debt` уносит весь тик метрик, и наружу сигнала нет

`open` · **живой дефект продакшен-кода**, не молчащая аннотация. Наследник `T717(б)` 007;
тот же класс, что `F-007-1` — уверенное ложное утверждение о данных.

**Механизм, измеренный по коду и прогонам.** Писатель складывает все семь ключей по всем
эквивалентам в один список и отправляет одним `executemany`
(`app/core/simulator/storage.py:497`) внутри одного SAVEPOINT (`:526-535`). Отказ одной строки
откатывает savepoint целиком, поэтому переполнение на `total_debt` уносит **весь тик метрик**, а
не одну точку и не одну серию. Тик не помечается flushed (`real_tick_persistence.py:150-152`), и
`flush_pending_storage` на остановке повторит тот же payload и упадёт так же. Bottlenecks идут
отдельным вызовом со своим savepoint (`storage.py:668-679`) и **не** затронуты — наружу пойдут
живые узкие места рядом с замороженными метриками.

**Достижимость.** `total_debt` — это `SUM(Debt.amount)` по эквиваленту
(`app/core/simulator/real_tick_metrics.py:62-68`), а денежная дверь 012 ограничивает по величине
**одно** значение (`abs(value) < 10**12`, `app/utils/validation.py:129`), не их сумму: двух долгов
у потолка достаточно, чтобы сумма его перешла.

**Отказ транзиторный, а не необратимый — «точка невозврата» опровергнута измерением.** Первая
редакция этой записи выводила из монотонности суммы долгов, что после пересечения падает каждый
следующий тик. Монотонность — утверждение о предметной области, а не о коде, и оно неверно: клиринг
долги гасит. Прогон эпизода `100 → 10^12 → 10^12 → 500 → 600` на PostgreSQL 16.9: тики 2000 и 3000 —
`write=False, rows=0/7`; тик 4000, где клиринг опустил долг до `500`, — `write=True, rows=7/7`,
**немедленно**. Залипающего состояния в коде нет, каждый тик берёт свой savepoint. Теряются ровно те
тики, что были над границей. Для severity это существенно: разовый самовосстанавливающийся сбой и
необратимая потеря — разные дефекты.

**Что видит клиент — воспроизведено.** Тик 1000 записан, тики 2000–5000 не записаны вовсе:
`GET /metrics` отдаёт HTTP 200 и **пять одинаковых точек** (`total_debt` → `'100.00000000'` ×5),
ни одного `null`, ни 503, ни поля `degraded`. Carry-forward ресемплера
(`app/core/simulator/metrics_bottlenecks.py:219-235`) делает «нет измерения» неотличимым от
«измерение повторилось».

**Граница проходит не там, где её называли — четыре точки на PostgreSQL 16.9**, колонка
подтверждена запросом как `numeric(20,8)`:

| вход | что делает `NUMERIC(20,8)` | тик |
|---|---|---|
| `999999999999.99999999` | проходит точно | записан целиком, `7/7` |
| `999999999999.999999994` | **молча округляется вниз** до `999999999999.99999999` | записан целиком, `7/7` |
| `999999999999.999999995` | **отвергается** — округление до scale 8 даёт ровно `10^12`, который не влезает в precision 20 | не записан целиком, `0/7` |

> **ПОПРАВКА 2026-08-25, внешний держатель развилки.** Строка выше верна для **произвольного**
> значения, поданного прямо в `NUMERIC(20,8)`, и **неверна как порог для `total_debt`**. Слагаемые
> читаются из `Debt.amount Numeric(20,8)`, а сумма считается в SQL (`func.sum`,
> `app/core/simulator/real_tick_metrics.py:64-68`), поэтому результат лежит на решётке с шагом
> `1e-8`: между `999999999999.99999999` и `1000000000000.00000000` **достижимого значения нет**.
> Для продакшен-пути порог остаётся ровно `10^12`. Пятинаноединичная полоса — свойство колонки, а не
> этой величины; утверждение «настоящий порог ниже `10^12`» было переусложнением, и это девятое
> ложное утверждение этой линии работ.
| `1E+12` | отвергается | не записан целиком, `0/7` |
| `1000000000000.00000001` | отвергается | не записан целиком, `0/7` |

**Округление применяется раньше проверки precision**, поэтому настоящий порог —
`total_debt >= 999999999999.999999995`, на пять нанoединиц ниже, чем `>= 10**12` в постановке
владельца и в прежней редакции `T717(б)`. Частичной записи нет ни в одной точке: тик либо весь,
либо ничего.

**Порог — свойство PostgreSQL; на SQLite дефекта нет вовсе.** Прогон на дефолтном для разработки
бэкенде (`app/config.py:54-57`) с `total_debt = Decimal("1E+12")`: запись успешна, персистятся все
7 строк, `GET /metrics` отдаёт `'1000000000000.00000000'`. `Numeric(20,8)` на SQLite — только
affinity. **Цена слепоты дефолтного тира — числом.** На пути метрик (`write_tick_metrics`, `build_metrics`,
`MetricsBottlenecks`, `SimulatorRunMetric`) собирается **55 тестов в 10 файлах: 45 в дефолтном тире
и 10 под маркером `postgres`**. Джоб PostgreSQL запускается только `-BackendMarker postgres`
(`.github/workflows/quality.yml:218-224`), поэтому эти 45 не гоняются против PostgreSQL **никогда** —
82% покрытия пути метрик живёт там, где дефект физически не существует. Сам джоб к тому же
`scheduled/manual` (`:157-158`): не идёт ни на push, ни на PR. Это аргумент не про 007, а про то,
чем вообще проверяются деньги.

**Наблюдаемых признаков отказа наружу — ноль. Перечислено исчерпывающе, а не предположено.**
Возвращаемое `False` потребляется ровно в двух местах, оба внутренние:
`real_tick_persistence.py:120` (маркер flushed, `:150-152`) и `:227` (повтор в
`flush_pending_storage`). Поле `_real_last_tick_storage_flushed_tick` приватное и не сериализуется
ни в один ответ (`models.py:215`; четыре сайта, все внутренние). Ветка отказа `storage.py:557-564`
не делает **ничего**, кроме `logger.exception`: не трогает `run.last_error` (четыре писателя —
платежи, клиринг, тик; ни одного в storage), не инкрементирует `errors_total`, не меняет `state`,
не эмитит событие. Артефакты — просто файлы из каталога прогона (`artifacts.py:122-147`). Положить
флаг некуда даже при желании: `MetricsResponse`, `MetricSeries`, `MetricPoint` и `RunStatus` — все
`ConfigDict(extra="forbid")` (`app/schemas/simulator.py:437`, `:496`, `:518`). Единственный след —
одна строка ERROR в серверном логе. Потребитель отличить «измерения не было» от «измерение

> **ПОПРАВКА 2026-08-25, внешний держатель развилки — и она меняет предмет починки.** «Положить флаг
> некуда» **неверно**: `extra="forbid"` запрещает НОВЫЕ поля, но канал сигнала уже существует и уже
> описан. `MetricPoint.v` объявлен nullable, и комментарий рядом определяет `null` именно как
> «измерения не было» — читатель сам эмитит `v=None` для точек до первого измерения, «never an
> invented 0.0» (`app/core/simulator/metrics_bottlenecks.py:219-222`). Дефект в том, что тот же
> читатель **выбрасывает сохранённые `NULL`** строкой `if r.value is None: continue` (`:215-216`),
> то есть уничтожает предусмотренный сигнал до того, как до него дойдёт carry-forward.
>
> Значит починка **не является изменением контракта** и предмет её другой:
> 1. проверять представимость агрегата до общего `executemany` и писать `NULL` для непредставимого
>    `total_debt`, **сохраняя остальные шесть ключей** (сейчас теряются все семь);
> 2. научить читателя сохранять явный `NULL` как разрыв линии, а не пропускать его;
> 3. контрпроверка на PostgreSQL: `999999999999.99999999 → 10^12 → 500` даёт семь строк на каждом
>    тике, `total_debt=null` на среднем, шесть остальных живы, затем восстановление;
> 4. политику CI для короткого PG-теста решать **отдельно** — перенос всего PG-тира на каждый PR
>    частью починки не является.
>
> Десятое ложное утверждение линии, тоже моё. Слепота гейтов остаётся сопутствующей корневой
> причиной пропуска, но чинить надо writer и reader, а не гейт вместо них.
повторилось» не может **никаким** способом; это и есть тяжесть находки.

**Прежние якоря 012 сошлись с прямым замером** (`app/utils/validation.py:117-121`;
`tests/integration/test_p012_t1201_magnitude_bound_is_load_bearing_postgres.py:54-55`;
`tests/integration/test_simulator_metrics_numeric_value_postgres.py:299-371` — пробой `1E+13`),
но ни один из них не описывал округление вниз, поэтому граница у всех названа на пять нанoединиц
выше настоящей. Замеры 2026-08-25 сделаны на своей базе `geov0_test_closure007` — создана,
отработана и удалена в том же прогоне.

### 2. Аннотации тика объявляют `float` там, где ходит `Decimal`

`open` · P3, поведение не затронуто. Наследник `T716(б)` 007, **с исправлением его формулировки.**

`app/core/simulator/real_tick_payments_coordinator.py:22` (поле dataclass), `:109` (локальная
переменная — тот самый словарь, который проходит весь тик) и
`app/core/simulator/real_tick_persistence.py:81` объявляют `dict[str, dict[str, float]]`.
Прежняя запись 007 утверждала, что значения «теперь `Optional[float]`». **Опровергнуто прогоном:**
в словарь ложатся `float` (`avg_route_length`, `active_participants`, `active_trustlines`) **и
`Decimal`** (`total_debt`, `clearing_volume` — `real_tick_metrics.py:107`, `:111-113`), а `None`
не ложится никогда: ни один сайт присваивания не пишет `None` (пять сайтов, все в
`real_tick_metrics.py:105,107,111,134,135`), «не измерено» выражается **отсутствием ключа** и
становится `NULL` только у читателя словаря (`storage.py:406-411`). Настоящий дрейф — `Decimal`,
а не `Optional`; правильная аннотация уже объявлена у соседа по цепочке —
`real_tick_metrics.py:35`.

Почему поведение цело: единственный потребитель словаря — `write_tick_metrics`, принимающий
`Decimal` по контракту (`storage.py:325`); в JSON словарь не уходит
(`run._real_last_tick_storage_payload` живёт только в памяти, два сайта —
`real_tick_persistence.py:89`, `:210`).

Почему ошибка молчит — **измерено, а не предположено:** `mypy` не встречается ни разу в
`.github/workflows/quality.yml` (единственный workflow репозитория) и отсутствует в
`requirements-dev.txt` — он даже не установлен.

### 3. Мёртвый параметр `utc_now` в `MetricsBottlenecks`

`open` · P3. Наследник `T716(в)` 007, жив ровно как описан.

`app/core/simulator/metrics_bottlenecks.py:65` присваивает `self._utc_now = utc_now`, и это
**единственное** вхождение `_utc_now` во всём файле; параметр стал мёртвым после `T714`, который
убрал запись из обработчика GET. Датированный комментарий на месте (`:62-64`). Единственный сайт
конструирования — `app/core/simulator/runtime_impl.py:133-140`; он был вне owner surface среза
2026-08-20, поэтому параметр оставлен, а не удалён.

## Найдено волной 013 — строгий декодер админки превращает аддитивное изменение сервера в отказ страницы

**Найдено 2026-09-11** перекрёстным ревью финальной дельты 013, проверено оркестратором по коду.

**Что установлено.** `admin-ui/src/api/realApi.ts` объявляет `included`/`truncated` закрытым списком
из трёх имён — это правка самой волны 013, и она верна: канон объявляет ровно эти три, продюсер по
построению других не выдаёт. Но `requestJson` считает промах схемы **фатальным для всего ответа**:
при несовпадении выбрасывается `INVALID_RESPONSE`, и страница графа не получает **ничего** —
ни участников, ни линий доверия, ни долгов. То есть четвёртое имя коллекции, добавленное сервером,
гасит экран целиком вместо того, чтобы обесценить одно поле метаданных.

**Достижимо при рассинхроне версий:** новый сервер, старый собранный бандл админки. Это обычное
состояние во время выкатки, а не экзотика.

**Почему не исправлено волной 013.** Это не свойство одного поля, а **политика декодирования всего
клиента**: сегодня она звучит «схема — контракт, промах фатален», и у неё есть основания — именно
она поймала бы расхождение канона и продюсера, ради которого волна и сужала тип. Менять её внутри
фикс-раунда чужой находки значило бы решить за весь клиент мимоходом. Варианты, между которыми надо
выбирать осознанно: `z.enum([...]).catch(…)` на этих двух полях; фильтрация неизвестных имён на
границе; или общее правило «метаданные деградируют, данные — нет». У каждого своя цена, и первая
из них ослабляет ровно тот гард, который волна только что поставила.

**Получателя нет.** 013 закрывает предмет и в её поверхность политика декодирования не входит; 016
объявляет `api/openapi.yaml` read-only и клиентских схем не касается. Поэтому запись здесь, как
факт отсутствия адресата, а не как передача.

## Найдено волной 013 — атрибуция платежа в админских метриках молчаливо считает «не участвовал»

**Найдено 2026-09-10** внутренним adversarial-проходом программы 013 и его фикс-раундом; проверено
оркестратором по коду.

**Что установлено.** `app/core/admin/metrics.py:692-697` определяет участие в платеже **только** по
`payload["from"]`/`payload["to"]`:

```python
if str(t_type) == "PAYMENT":
    if isinstance(pl, dict):
        from_pid = str(pl.get("from") or "")
        to_pid = str(pl.get("to") or "")
        involved = from_pid == participant_pid or to_pid == participant_pid
```

Ни отката на `initiator_id` (как это сделано ветвью `CLEARING` строкой ниже), ни признака «строку не
удалось атрибутировать». Значит платёж, чей внутренний payload этих ключей не несёт, попадает в
счётчик участника как **уверенный ноль**, а не как «неизвестно».

**Почему это ровно предмет 013, но не её поверхность.** Программа 013 — про экран, показывающий
значение там, где значения нет; здесь то же самое одним этажом выше, в продюсере метрик. Клиентская
половина уже научена отличать «нам не сказали» от «сказали ноль» и помечать неатрибутируемые строки
(`admin-ui/src/composables/useGraphAnalytics.ts`), но **над серверным счётчиком она бессильна**: тот
приезжает готовым числом без признака полноты. `## Owner surface` 013 включает фронтенды и один
графовый продюсер; `app/core/admin/metrics.py` в неё не входит.

**Живой получатель есть, и это 016.** Её `F-016-8` называет ровно этот диапазон —
`app/core/admin/metrics.py:569-735` — как расходящуюся копию алгоритма активности. То есть находка не
«без владельца», а ждёт авторизации 016.

**Почему запись здесь, а не в спеке 016.** На момент записи `specs/016-duplicate-policy-owners/`
**не закоммичена** — она лежит в общем рабочем дереве как незакоммиченная работа соседней сессии.
Дописывать в чужой файл в полёте — ровно тот механизм, которым в этом репозитории уже один раз
перемешали две работы в одном коммите. Поэтому указатель лежит здесь; когда 016 будет слита, находка
переносится в её тело одной строкой.

## Без владельца с 2026-08-24 — `api/openapi.yaml` после закрытия программы 011

**Названо 2026-09-10**, поводом послужил вердикт внешнего ревью `VERDICT-AUTHORITY: OUT-OF-SURFACE`
по правке канона волной 012.

**Что установлено.** `## Owner surface` программы 012 запрещает трогать `api/openapi.yaml` с прямым
указанием владельца — «владелец 011». Программа 011 **закрыта 2026-08-24**. То есть запрет действует,
а адресат, к которому он отсылает, больше не существует: закрытая программа не принимает правок, не
ведёт ревью и не отвечает за контракт.

**Почему это не формальность.** У репозитория `api/openapi.yaml` объявлен авторитетом №1 для
REST-контракта (`specs/README.md`), а `AGENTS.md` §8 требует менять его согласованным набором —
реализация + схема + поведенческие тесты + документация. Набор можно собрать; чего нельзя собрать без
владельца — это **решения о том, что канон должен обещать**. Беспризорный контракт не блокирует
работу, он просто делает каждую правку канона правкой без ревьюера по существу.

**Прецедент, уже стоивший волне.** Это второй раз, когда закрытая программа названа владельцем
предмета: 012 однажды передала форму денег в 011 записью «это её поверхность», 011 закрылась класс не
взяв, и находка пролежала потерянной всю волну. Правило, выведенное тогда, применяется и здесь:
проверять надо не «названа ли граница», а «кто это возьмёт и жив ли он».

**Что сделано, а что нет.** 012 правит канон осознанно и согласованным набором по §8, и факт правки
записан в её `T1213`. Не сделано и здесь не решается: кто ведёт `api/openapi.yaml` дальше. Кандидаты
— живая программа с контрактной поверхностью либо отдельная строка в `AGENTS.md` §8; выбор шире 012.

## Долг без владельца после `T1213` волны 012 — `HOUR` объявлен с разной точностью в двух наборах

**Найдено внешним ревью 2026-09-10** (`gpt-6-astra`, medium) при проверке дельты `T1212`. К сужению
`Equivalent.precision` отношения не имеет: расхождение старше, сужением не создано и не лечится —
обе величины внутри нового домена `0..8`.

**Замер, пересчитан на дереве 2026-08-25:**

| Набор | `HOUR` |
|---|---|
| `seeds/equivalents.json` | `precision: 1` |
| `admin-fixtures/v1/datasets/equivalents.json` | `precision: 2` |
| `admin-fixtures/packs/greenfield-village-100-v2/v1/datasets/equivalents.json` | `precision: 2` |
| `admin-fixtures/packs/riverside-town-50-v2/v1/datasets/equivalents.json` | `precision: 2` |
| `admin-ui/public/admin-fixtures/v1/datasets/equivalents.json` | `precision: 2` |

**Почему это не косметика.** `precision` — минимум знаков вывода, поэтому один и тот же час
обязательства печатается как `1.5` на стенде, засеянном из `seeds`, и как `1.50` на стенде,
засеянном из фикстур админки. Волна 012 закрывала ровно этот класс — «одна величина, разные формы у
разных производителей», — но там расходились ПРОИЗВОДИТЕЛИ на одной строке, а здесь расходятся
ДАННЫЕ, и ни один гард волны такого не ловит: каждый набор внутренне консистентен.

**Чего здесь нет — решения.** Какая из двух точностей верна для часа, определяется продуктом, а не
кодом: `1` говорит «час делится на десятые», `2` — «на сотые». Обе представимы и обе сохраняемы.

**Получателя нет, и это регистрируется как факт, а не как адресат.** 012 закрывает представление, а
не содержание поставляемых наборов; 013 (фронтенд) данные не владеет; 015 исключает поверхность 012.
Поэтому долг лежит здесь, а не «передан».

### 2026-09-11 — `MISSED-3`: передача в 012 заявлена, получение не видно

Найдено при перепроверке `T1400` программы 014. Разбор 008 перевёл `MISSED-3` из группы `AA` в
группу `Q`, то есть **в программу 012**, и на этом основании 014 исключила его из своей области
(`specs/008-surface-code-review/tasks.md:483-485`, `specs/014-false-green-guards/spec.md`).

**Но ни строка `MISSED-3`, ни `test_trustlines_list_filters_pagination` не встречаются нигде под
`specs/012-money-precision-and-representation/`.** Программа 012 закрыта 2026-09-11 записью выше.
Передача заявлена двумя документами и не видна в третьем — это долг, а не закрытие, и он записан
здесь именно потому, что обе стороны считают вопрос чужим.

**Получателя нет.** 014 его исключила по существу верно: у неё другая группа. Открывать программу
под одну позицию несоразмерно. Диспозиция принимается владельцем.

### 2026-09-11 — `admin-ui/public/admin-fixtures/v1/api-snapshots/` не читает никто

Найдено при `T1402` программы 014, когда проверялось, где ещё публикуется снятая zero-sum.

`admin-ui/src/api/mockApi.ts:395` читает `datasets/integrity-status.json`. Соседняя директория
**`api-snapshots/`** (двенадцать файлов) не упоминается **нигде** в исходниках — ни в
`admin-ui/src`, ни в `admin-ui/scripts`, ни в тестах, ни в e2e; единственное вхождение во всём
дереве — артефакт отчёта о дублировании в `.local-run/`.

**Хуже, чем «мёртвый файл».** `api-snapshots/integrity.status.get.json` описывает форму ответа,
которой сервис **никогда не возвращал**: `checks_total`, `checks_failed` и массив `checks` с
именами `zero_sum_by_equivalent`, `limits_consistency`, `orphan_participants`. Настоящий
`GET /api/v1/integrity/status` отдаёт `equivalents` с `invariants`. То есть в репозитории лежит
образец контракта, который не соответствует ни реальности, ни канону, и ничем не проверяется.

**Не трогается здесь.** 014 владеет снятием zero-sum, а не уборкой неиспользуемых фикстур, и
исключение для `T1402` записано узко именно чтобы не расползаться. Диспозиция — владельцу:
удалить директорию, либо подключить и привести к канону, либо пометить историческим.
**Получателя нет.**

### 2026-09-11 — оценка направления внешним ревьюером: `PARTIALLY-DRIFTED`

Запрошена владельцем: «правильно ли мы движемся к главной задаче — нулевая сумма всех долгов, 100%
надёжность и идемпотентность транзакций, или ушли в сторону». Ревьюер: Codex `codex-cli 0.154.0`,
`-m gpt-6-astra`, `model_reasoning_effort=high`, `--sandbox read-only`, дерево на `4a6f333`, exit `0`.
Усилие поднято до `high` против обычного `medium`: вопрос стратегический, не диффовый.

**Маркеры:** `DIRECTION: PARTIALLY-DRIFTED`, `ZERO-SUM-SEQUENCING: CORRECT`,
`IDEMPOTENCY-ESTABLISHED: PARTIAL`, `NEXT-PROGRAMME: 015`, `SUSPEND-014: YES`.

**Где именно произошёл поворот — названо точкой, а не настроением.** Решение 2026-08-21 поставить
верификацию финансового ядра **после всей волны 009–014**, при том что тавтология уже была
установлена (`research-brief.md:8`). И тот же дрейф **воспроизведён внутри самой 015**: её
центральная сверка — волна 4, позади маршрутизации, представления, агрегатов и подписи
(`015/spec.md:366`). Формулировка ревьюера: «нумерация программ стала более сильным правилом
планирования, чем сама цель».

**Снятие тавтологии признано верным** (`ZERO-SUM-SEQUENCING: CORRECT`) — держать ложную уверенность
до появления замены значило бы продлевать искажение. Но с двумя поправками, которые важнее вердикта:

1. **«Защиты теперь нет» — неверно.** Платёж по-прежнему проверяет лимиты доверия, симметрию
   встречных долгов и дельты участников до коммита (`app/core/payments/engine.py:1255`); клиринг
   проверяет нейтральность участников (`app/core/clearing/service.py:2042`).
2. **Журнал 015 необходим, но НЕ достаточен.** Ошибочный писатель запишет одну и ту же неверную
   дельту и в журнал, и в `Debt` — сверка пройдёт. И сверка от baseline не удостоверяет корректность
   долга, уже существовавшего на baseline. Отсюда: у 015 обязаны быть **два разных критерия приёмки**
   — согласие журнала с состоянием и корректность операции против независимо заданного эталонного
   расчёта. Ни один из них в одиночку не называется «100% финансовая корректность».

**Уточнение формулировки цели, и оно не филологическое.** «Нулевая сумма всех долгов» — валовые долги
суть положительные обязательства, а нулю тождественно равна сумма **нетто-позиций**. Владельцу нужна
гарантия, что **обязательства именно те, какие должны быть**, — утверждение строго сильнее, и именно
оно не обеспечено ничем.

**Идемпотентность — `PARTIAL`, и это проверено по коду, а не оценено.** Публичная идентичность — не
`Idempotency-Key` (он принимается и игнорируется на публичном маршруте,
`app/api/v1/payments.py:89`), а обязательный подписанный `tx_id`. Сильнейшее evidence —
Postgres-набор реплея клиринга с реальной конкуренцией, проверкой ровно одного коммита, точных
остатков долга и контрольным случаем «новый цикл всё-таки исполняется»
(`tests/integration/test_clearing_commit_replay_postgres.py:587,772`). Слабейшее —
`tests/unit/test_payment_timeouts.py:182`: подменяет prepare пустышкой, а commit — функцией, которая
лишь метит транзакцию `COMMITTED`, и **никогда не применяет долговой эффект**; доказывает
восстановление ответа, а не финансовую атомарность. Публичный тест дубликата
(`tests/integration/test_payments_idempotency.py:75`) сверяет id и статус и **не смотрит на долг
после реплея**.

**Три границы, проверенные мной по коду вслед за ревьюером:**

- **`app/api/v1/integrity.py:404-436` — ремонт удаляет реальный долг, если линия доверия
  ЗАМОРОЖЕНА.** Выборка идёт по `TrustLine.status == "active"`, отсутствующий ключ даёт лимит `0`, и
  долг сносится. Это записано как намеренное в собственном docstring эндпоинта: «If a debt has no
  active trustline (limit treated as 0), the debt is removed». Уже заведено как **`F-015-6`, `P1`**
  (`015/spec.md:76`, задача `T1511`) — и **лежит неавторизованным с 2026-08-24**.
- **`app/core/payments/engine.py:1552` — дрейф ровно в один квант хранения ПРИНИМАЕТСЯ.**
  `tolerance = Decimal("0.00000001")`, сравнение строгое `abs(drift) > tolerance`. Это ровно тот
  предел обнаружения, о который бьётся формулировка владельца «ошибка будет накапливаться».
  Механизм записан в `012/spec.md:147-148` как объяснение, почему `F-012-1` ничем не ловится;
  **владельца у самой границы нет**, и она обязана стать явным решением приёмки в 015.
- **`app/core/payments/service.py:214` — НОВАЯ НАХОДКА, не записанная нигде.** Сравнение отпечатка
  запроса стоит под условием `existing_fp is not None`. Транзакция, сохранённая **без** отпечатка,
  сравнение равенства запросов **обходит целиком**: повтор с другим запросом под тем же `tx_id`
  вернёт сохранённый результат вместо конфликта. Ревьюер выполнил изолированный зонд резолвера:
  при отсутствующем отпечатке — `STORED_RESULT` на другой запрос, при заполненном — конфликт.
  **Установлено поведение ветки, не существование затронутых строк в продакшене.** Получателя нет.

**Рекомендованный порядок работ** (решение за владельцем, здесь записано как вход):

1. Остановить очередь 014, сохранив сделанное и его evidence.
2. Сначала **сдержать вредоносных писателей**: `T1511` (ремонт) и `T1514` (округление уже сохранённых
   `Debt`/`TrustLine`), с ограничением использования ремонтного эндпоинта до исправления.
3. Дальше `T1501`–`T1505` **одним вертикальным срезом**: baseline, точные версионированные дельты,
   атомарная запись журнала и состояния, охват **всех** писателей `debts`, сверка. Неинструментированный
   писатель обязан быть заблокирован либо явно делать верификацию недоступной.
4. Вместе с этим — `T1516` и политика реакции: обнаружение без сдерживания не отвечает на «ошибка
   будет накапливаться». Недоступный верификатор не имеет права давать успех.
5. Матрица приёмки транзакций (идемпотентность и надёжность) параллельно, с внесённым повреждением:
   один квант, равномерное раздутие цикла, пропущенная и задвоенная дельта, откат, реплей.
6. 016 **не бросать целиком и не начинать целиком**: `T1603` (извлечение SQLSTATE) может поддержать
   матрицу, его integrity-часть пересекается с 015; UI- и query-дедупликация откладывается.

**О методе — принято как справедливое.** Самонанесённые находки не доказывают, что итог хуже: ревью
ловило настоящие дефекты, и несколько финансовых механизмов стали ощутимо крепче. Но повторяющиеся
циклы починки evidence, исчерпания круга ревью и получения исключения на закрытие означают, что
**административное завершение съедает существенную часть усилий, не устанавливая финансовую цель**.
Предложено: заменить вехи «программа закрыта» на наблюдаемые финансовые способности (повреждение
обнаружено, неверный эффект отвергнут, неоднозначный коммит разрешён, повторный запрос применён
один раз); проверять инвариант и его независимый оракул **до** широкой реализации; и **два
безуспешных круга ревью должны запускать сужение среза, а не превращаться в принятие риска**.

**Поправки ко мне, принятые:** CI шире, чем я сказал ревьюеру, — на push/PR идут **четыре** джобы
(`required-quality`, `static-diagnostics`, `dev-image-content`, `ui-smoke`), а не одна smoke;
Postgres, оба E2E и super-smoke остаются `scheduled/manual`. Паттерн принятия риска включает и **011**,
не только 012 и 013. «001–008 закрыты» неточно: у 007 закрыта реализация при остаточном долге, у 008
фаза II открыта. `RT-009-5` — про создание линии доверия, а не про платёж.

### 2026-09-11 — имена ограничений внешних ключей зависят от того, как создана база

Найдено при `T1524` программы 015: миграция `020`, удалявшая `fk_debts_equivalent_id` по имени, которое
создаёт миграция `005`, упала на `geov0_test_ci` с `constraint "fk_debts_equivalent_id" does not exist`.
Там ограничение называлось `debts_equivalent_id_fkey` — имя по умолчанию PostgreSQL для безымянного
`ForeignKey`, объявленного моделью. Такое имя получает схема, созданная `Base.metadata.create_all` и
затем помеченная `alembic stamp`, а не прогнанная миграциями с `001`.

**Следствие:** одна и та же голова миграций описывает базы с разными именами ограничений, и **каждая
будущая миграция, удаляющая ограничение по жёстко заданному имени, упадёт на одной из сред** —
причём тихо проходя на той, где её писали. `020` обходит это отражением ключа; остальные ключи `debts`
на участников и ключи других таблиц тем же образом не проверялись.

**Корень** — модели объявляют `ForeignKey(...)` без `name=` и без `naming_convention` у `MetaData`,
тогда как миграции дают имена явно. Диспозиция — владельцу: ввести `naming_convention` и привести
базы к одним именам, либо закрепить правило «удалять ограничения только через отражение». **Получателя
нет.**

### 2026-09-11 — Postgres-тир тестов работает не в том уровне изоляции, что приложение

Найдено при шаге 3 фазы B программы 015. Приложение создаёт движок PostgreSQL с
`isolation_level=settings.DB_POSTGRES_ISOLATION_LEVEL` — `SERIALIZABLE` (`app/db/session.py`). Общий
тестовый движок (`tests/conftest.py`, `create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)`)
уровень не задаёт и работает в `READ COMMITTED` по умолчанию сервера.

**Следствие:** всё, что зависит от снимка `SERIALIZABLE` — ошибки `40001`, снимок, зафиксированный до
ожидания лока, повтор единицы работы, — Postgres-тир **не видит**, хотя приложение живёт именно в этом
режиме. Тест, зелёный в `READ COMMITTED`, может описывать поведение, которого у приложения нет, и
наоборот. Второй слой того же рода: `db_session` на Postgres оборачивает тест во внешнюю транзакцию,
и транзакционные advisory-локи под ней **не снимаются ни одним «commit»** — стенд не отличает
писателя, державшего лок, от потерявшего его.

**Обход в 015:** стенд шага 3 строит свой движок `SERIALIZABLE` с настоящим пулом. Сам общий движок
не менялся: перевод всего тира на `SERIALIZABLE` поменяет поведение 136 Postgres-тестов и требует
отдельного прогона и разбора. **Получатель — программа 015, шаг 8** («явные Postgres-гейты приёмки для
границ локов, повторов…»), где это условие приёмки, а не улучшение.

## Отложено из 015 при задании закрытия — 2026-09-14

**Основание:** вопрос владельца 2026-09-14 «не ушли ли мы в бесконечное шлифование»; ответ по числам и
правило §19.5 AGENTS.md, введённое тем же днём; раздел `F` в `## Порядок исполнения` спеки 015. Каждая
строка ниже — находка класса 2 по §19.5: ни одна не называет воспроизведённую потерю на денежном пути.
Строки остаются в таблице задач 015 с пометкой `→ BACKLOG 2026-09-14` и закрытия программы не блокируют.

**Как строка отсюда возвращается в работу:** только заявкой с письменными ответами на шесть вопросов
§19.2 и с репродьюсером потери, если заявляется класс 1. Номер задачи не переиспользуется.

| Задача | Что осталось | Получатель |
|---|---|---|
| `T1507` | выровнять `/integrity` API, OpenAPI и протокол; английский канон всё ещё называет zero-sum проверкой (с `T1542`) | нет; берётся только по заявке с ответами §19.2 |
| `T1510` | остаток `F-015-8`: политика и `max_paths` роутера в предсказательных маршрутах; защита от собственного PID уже сделана `T1545` | нет; берётся только по заявке с ответами §19.2 |
| `T1511` | сама починка ремонта (`F-015-6`); оба эндпоинта закрыты `409`/`E008`, потеря сдержана | нет; берётся только по заявке с ответами §19.2 |
| `T1512` | `participants/service.py` и `admin/metrics.py` складывают разные эквиваленты; представление, не движение денег | нет; берётся только по заявке с ответами §19.2 |
| `T1513` | форма денежной строки на выходе `/trustlines`, `/payments`, `/capacity`, `/max-flow`; `min_scale`-ловушка | нет; берётся только по заявке с ответами §19.2 |
| `T1514` | остаток: инжект при обратном долге, PID вне сценария; сдерживание инжекта дало `T1544` и шаг 5 | нет; берётся только по заявке с ответами §19.2 |
| `T1515` | планировщик симулятора обещает ёмкость выше расчёта ядра; реализм симулятора | нет; берётся только по заявке с ответами §19.2 |
| `T1517` | контракт подписи: связывание типа операции; потеря денег не воспроизведена | нет; берётся только по заявке с ответами §19.2 |
| `T1518` | остаток `F-015-14`, четыре строки расхождений с протоколом; предмет спора — документ | нет; берётся только по заявке с ответами §19.2 |
| `T1519` | мёртвый код: `validate_idempotency_key`, `PaymentService.get_payment`, `PaymentDetail` и др. | нет; берётся только по заявке с ответами §19.2 |
| `T1521` | два режима округления денег (`F-015-16`); денежного расхождения на живом пути не измерено | нет; берётся только по заявке с ответами §19.2 |
| `T1531` | перечитывающий `SELECT` журнала перезаписываем слушателем; противник внутри процесса, припаркована 2026-09-13 | нет; берётся только по заявке с ответами §19.2 |
| `T1536` | слушатель после охранника переадресует UPDATE метаданных долга; противник внутри процесса, припаркована 2026-09-13 | нет; берётся только по заявке с ответами §19.2 |
| `T1538` | проверенную запись журнала можно удалить после проверки; противник внутри процесса | нет; берётся только по заявке с ответами §19.2 |
| `T1539` | учёт savepoint-ов `T1532` даёт ложный отказ при трассирующем слушателе; в продакшене такого слушателя нет, гипотеза §18 | нет; берётся только по заявке с ответами §19.2 |
| `T1541` | CI строит схему трижды, около 17 s на плановом job | нет; берётся только по заявке с ответами §19.2 |
| `T1542` | `docs/en/03-architecture.md` рисует DDL `debts` без `ON DELETE`; сливается с `T1507` | нет; берётся только по заявке с ответами §19.2 |
| `T1547` | real-mode симулятора пишет в общую `debts` без отдельного opt-in; стоп эквивалента `T1544` уже связывает симулятор | нет; берётся только по заявке с ответами §19.2 |
| `T1552` | `POST /clearing/auto` возвращает необъявленный `400` при выключенном клиринге; решено `409`/`E008` через узел `T1544`, бриф готов, только код | нет; берётся только по заявке с ответами §19.2 |
| `T1554` | подмена `checksum_after` при сбое контрольной точки в платеже и клиринге; выделена ключевым ревью шага 5 | нет; берётся только по заявке с ответами §19.2 |
| `T1555` | `_resolve_inject_debt_equivalent_ids` перебирает все эквиваленты; `SCOPE-EDGE-CASE-ONLY` по ревью Codex | нет; берётся только по заявке с ответами §19.2 |
| `T1556` | распределённый лок планового цикла целостности; `COST: FOLLOW-UP` по ревью Codex | нет; берётся только по заявке с ответами §19.2 |
| `T1549`-находка 1 | одновременное `POST /api/v1/trustlines` одной тройки на `SERIALIZABLE` отвечает `[201, 500]` вместо `[201, 409 CONCURRENT_TRUSTLINE_CREATE]`: `40001` не обрабатывается, `TrustLineService.create` ловит только `IntegrityError` (`app/core/trustlines/service.py:236`, `:292`); той же формы, не измерено, `app/core/participants/service.py:82`. Дубликата нет, неверен статус. Тест `test_p1_trustline_reopen_postgres.py::test_concurrent_create_of_the_same_triple_yields_one_line_and_a_declared_conflict` помечен `xfail(strict=True)`; репродьюсер `specs/015-financial-core-verification/closure-briefs/evidence/repro_trustline_40001.py` | нет; берётся только по заявке с ответами §19.2 |
| `T1549`-находка 2 | тесты, явно закреплённые на `READ COMMITTED` и не названные контрпробами, остались как есть: `test_clearing_skip_releases_locks_postgres.py:374`, `test_concurrent_clearing_payment_lost_update_postgres.py:183`, `test_concurrent_prepare_routes_bottleneck_postgres.py:119`, `test_payment_commit_advisory_locks_postgres.py` (`:135`, `:345`, `:1024`, `:1142`, `:1268`), `test_payment_idempotency_postgres.py:118`. Копии на `SERIALIZABLE`: проходят все, кроме двух — одновременные клиринг и платёж (`INSERT INTO transactions` клиринга получил `40001` и вышел как `Internal server error`) и узкое место маршрутов (`TimeoutError`, барьер считает вызовы лока); **дефект ли это приложения — не установлено**, потери денег не показано | нет; берётся только по заявке с ответами §19.2 |

**Программа 016 (`specs/016-duplicate-policy-owners/`) не авторизуется** решением 2026-09-14: восемь P2
дублирования политики не отвечают на вопрос 1 §19.2 (наблюдаемая потеря), а в роадмапе `README.md`
стоят непройденные продуктовые фазы 2–4. Спека остаётся как запись аудита; задачи не переносятся.

## Найдено срезом стадии 1 программы 017 — 2026-09-21

Обе находки родились от одного: обязательный гейт впервые поехал на PostgreSQL, и то, что скрывал SQLite, стало видно. Ни одна не держит 017 — по §19.5 это класс 2, деньги и долг по ним не движутся неправильно.

### 2026-09-21 — половина прогонов симулятора не сохраняется на PostgreSQL: `seed` объявлен int32, а генерируется в 32 бита без знака

**Получатель: владелец.** Предполагаемый исполнитель — 021 (симулятор как клиент домена) либо отдельная узкая правка; не авторизовано.

`simulator_runs.seed` объявлен `sa.Integer` и в модели (`app/db/models/simulator_storage.py:31`), и в миграции (`migrations/versions/013_simulator_storage_mvp.py:32`), то есть `integer` с потолком 2³¹−1. Значение — первые четыре байта SHA-256 от `run_id` (`app/core/simulator/run_lifecycle.py:168-169`, `int.from_bytes(seed_material[:4], "big")`), то есть равномерно в `[0, 2³²)`. **Верхняя половина диапазона в колонку не влезает**, и вставка падает: `invalid input for query argument $11: 3122177940 (value out of int32 range)`.

Вероятность отказа — примерно **половина** прогонов, и она не зависит ни от нагрузки, ни от сценария: она определена одним хешем идентификатора прогона.

**Почему это не было видно.** На SQLite `INTEGER` 64-битный, поэтому дефект не проявлялся ни разу за всё время, пока дефолтный тир шёл на SQLite. Это ровно правило §15 AGENTS.md «проверяйте, способен ли стенд увидеть тот исход, ради которого построен» — и первый случай, когда перевод гейта на авторитетный движок сам предъявил дефект, а не гипотезу о нём.

**Репродьюсер** (§13 требует падающий на текущем коде): `tests/integration/test_simulator_super_smoke.py` на живом Postgres 16 — `2 failed, 1 passed`, exit 1. Команда в разделе о `simulator-super-smoke` ниже.

**Чего эта запись не утверждает.** Правильная ширина колонки не выбрана: `BigInteger`, сужение генерации до 31 бита и хранение как строки дают разные последствия для существующих строк и для воспроизводимости прогонов. Это развилка миграции, и по протоколу решений 2026-09-21 она решается консультацией Codex, а не по умолчанию.

### 2026-09-21 — `simulator-super-smoke` не получил сервис Postgres: job стал бы заведомо красным

**Получатель: 019 и владелец записи выше.** Задача `T1701` требовала дать job'у сервис и URL в том же срезе. **Сделано не было, и это измеренный отказ, а не пропуск.**

Прогон селектора на живом Postgres даёт два независимых падения, оба вне поверхности `T1701`:

1. переполнение `seed` из записи выше;
2. `SerializationError: could not serialize access due to read/write dependencies … Canceled on identification as a pivot` на обычном `SELECT participants` внутри реального тика — ретрая на этом пути нет. Это поверхность 019 (платёж одной транзакцией и предикаты ретрая).

Дать job'у URL сегодня значило закоммитить job, который обязан падать. В репозитории уже есть один красный расписанный job, и второй превратил бы расписание в шум. Условие, при котором сервис и URL туда приезжают, записано прямо в `quality.yml` рядом с job'ом вместе с обеими причинами.

**Команда замера:**

```powershell
$env:TEST_DATABASE_URL = "postgresql+asyncpg://geo:geo@127.0.0.1:5432/geov0_test_<slug>"
$env:GEO_TEST_ALLOW_DB_RESET = "1"
.\scripts\verify_local.ps1 -TaskSlug <slug> -BackendOnly `
  -BackendSelector tests/integration/test_simulator_super_smoke.py -IncludeExpensive
```

### 2026-09-22 — архивный legacy-генератор сценариев сломан переносом структуры в описание

**Получатель: слайс удаления seed-генераторов** (стадия 3 программы 017 либо отдельный cleanup). Класс 2 по §19.5.

`scripts/_archive/generate_simulator_seed_scenarios_legacy.py:19` загружает активный генератор как модуль и зовёт из него `_convert_to_scenario`, `_assert_basic_integrity`, `_seed_module_by_path`, `_scenario_from_seed_module` (`:37-88`). `T1712` переписала активный генератор на чтение `seeds/communities/<id>/community.json`, и всех четырёх имён больше нет. Импорт по-прежнему проходит, а вызов любой из четырёх функций `generate_*` даёт `AttributeError`.

**Почему не починено здесь.** Файл лежит под `scripts/_archive/` — защищённая read-only поверхность (§3), и он и так зависел от генераторов, которые более поздний слайс удаляет. Чинить его сейчас значило бы продлевать жизнь тому, что уходит. Удаление — отдельный cleanup-класс со своим reference scan (§14).

**Что проверено:** его никто не зовёт — ни CI, ни npm-скрипт, ни тест. `tests/unit/test_simulator_scenario_allowlist_and_archives.py` утверждает лишь, что архивные **сценарии** не загружаются, про этот скрипт он молчит.

### 2026-09-22 — документы симулятора описывали прежний вход генератора сценариев

**Закрыто тем же PR**, запись оставлена, чтобы расхождение не переоткрывали как новое. `docs/ru/simulator/backend/fixtures-mapping.md` называл входом генератора seed-модули и выводил `groupId` из диапазонов номеров в PID (раздел 3.2) — ровно тот вывод смысла из формы, против которого написан §9 `AGENTS.md`; `scenarios-and-engine.md` вёл стрелку схемы от канонических admin-фикстур; `realistic-scenarios.md` указывал на функцию внутри генератора. Все три приведены к действующему пути, прежнее описание помечено историческим, а не удалено.

Попутно измерено и записано: `can_be_intermediate` решает **кредитор один**, а не пара «business ↔ business», как утверждали оба `*-v2.md` — 147 линий `business → person` против 48 в Greenfield, 63 против 18 в Riverside.

### 2026-09-22 — заморозка участника не мешает маршруту идти через него

**Получатель: владелец.** Класс по §19.5 пока **2**, и ровно по одной причине: репродьюсера нет. Если он напишется, находка становится классом 1 — это денежный путь.

Протокол объявляет статус участника `suspended` и переводит его как «временно заморожен» (`docs/ru/02-protocol-spec.md:122,134`). В коде **ни `app/core/payments/`, ни `app/core/clearing/` не обращаются к статусу участника ни разу** — проверено сплошным поиском 2026-09-22. Роутер отбирает рёбра только по статусу линии: `TrustLine.status == 'active'` (`app/core/payments/router.py:195-200`).

Следствие: замороженный участник остаётся полноправным **транзитным узлом**. Платёж между двумя активными участниками может пройти через него, создав ему долг и израсходовав его линии, пока он заморожен.

**Чего эта запись не утверждает.** Что протокол запрещает транзит через `suspended` — прямой формулировки об этом я не нашёл, найдено только само определение статуса. Возможны три исхода: (а) транзит запрещён и это дефект денежного пути; (б) транзит разрешён намеренно и тогда слово «заморожен» вводит в заблуждение; (в) вопрос не решён вовсе. **Прежде чем чинить — решить, какой из трёх**, и записать решение датой.

Найдено при написании рецепта `T1713`: рецепт может запретить *называть* замороженного участника после заморозки, но помешать роутеру идти *через* него не может.

### 2026-09-22 — статус трастлайна `frozen` объявлен протоколом и недостижим продуктом

**Получатель: владелец.** Класс 2, репродьюсера нет.

Протокол объявляет `"status": "active | frozen | closed"` для линии (`docs/ru/02-protocol-spec.md:174`) и прямо строит на нём расчёт лимита: `AND tl.status IN ('active', 'frozen')` с комментарием «frozen сверяется с сохранённым лимитом» (`:1833-1834`). То есть замороженная линия обязана сохранять лимит и участвовать в проверках.

В коде `TrustLineService` пишет ровно два значения — `'active'` при создании (`app/core/trustlines/service.py:221`) и `'closed'` при закрытии (`:494`).

**Поправка 2026-09-22, в тот же день:** первая редакция этой записи утверждала, что записи `'frozen'` нет нигде. **Это неверно, и неверная посылка опаснее отсутствующей** (§15). Писатель существует ровно один — `app/core/simulator/inject_executor.py:934`, инжектор инцидентов симулятора, и он ставит `frozen` линиям участника в той же транзакции, где ставит ему `suspended` (`:932-934`). Верное утверждение уже: статус достижим **только** через инъекцию инцидента симулятора и **никогда** через доменный сервис трастлайнов. Найдено исполнителем `T1711` при проверке моего же решения.

**Отсюда вторая находка, которой в первой редакции не было: слово «заморозка» означает в продукте две разные вещи.** Админский путь `_set_participant_status` (`app/api/v1/admin.py:940-975`) трогает **только** `participants.status` и линий не касается. Путь симулятора замораживает участника и его линии вместе. Один и тот же термин, два разных исхода в БД — и оператор, читающий «заморожен», не может по слову понять, какой именно.

**Поправка 2026-09-23 — предыдущая фраза этого абзаца была ложной посылкой, и опасной.** Она утверждала, что роутер, исключающий замороженные линии, «расходится» с `docs/ru/02-protocol-spec.md:1833-1834`. Эти строки — **проверка инварианта лимита для уже существующего долга**: замороженная линия сохраняет лимит, закрытая или отсутствующая даёт ноль; реализовано ровно так в `app/core/invariants.py:116`. Про маршрутизацию **новых** платежей там нет ни слова. Исключать замороженную линию из маршрута — **верное** поведение: заморозка останавливает новый поток, сохраняя старый долг в его лимите. Прежняя формулировка приглашала «починить» роутер так, чтобы он пускал платежи через замороженные линии, то есть сломать правильное. Найдено закрывающим внешним ревью стадии 1 (находка F12). Настоящий и единственный разрыв остаётся прежним: доменный сервис **не умеет создать** замороженную линию.

Практическое следствие уже видно: описание `seeds/communities/greenfield-village-100/community.json` объявляет **9 замороженных линий**, и привести базу в это состояние `T1711` сможет только прямой записью в колонку мимо доменного сервиса — ровно тем, что программа 017 из кода убирает.

**Развилка та же, что и выше:** либо продукт обязан уметь замораживать линию и тогда это пробел реализации, либо статус из протокола ушёл и тогда неверен протокол. Решать до того, как `T1711` напишет обходной путь.

### 2026-09-23 — лаунчер не может поднять бэкенд на машине с обычным venv: владение по PID не выполнимо

**Получатель: владелец.** Класс 2 по §19.5 — деньги не движутся, но **ежедневный цикл владельца сломан**, и это не регрессия `T1710`: воспроизведено на немодифицированном `run_full_stack.ps1` с `origin/main`.

`\.venv\Scripts\python.exe` на Windows — это **venvlauncher-заглушка**, а не копия интерпретатора: 274 712 байт против 103 192 у базового `python.exe`, содержимое разное. Она порождает базовый интерпретатор **дочерним процессом**, поэтому слушатель порта никогда не равен запущенному PID.

**Воспроизведено оркестратором независимо 2026-09-23**, вне лаунчера:

```powershell
$p = Start-Process -FilePath ".\.venv\Scripts\python.exe" -ArgumentList "-m","http.server","18399","--bind","127.0.0.1" -PassThru
(Get-NetTCPConnection -LocalPort 18399 -State Listen).OwningProcess   # != $p.Id
```

Результат: запущенный PID 16120, слушатель 28012, имя процесса `python`. `Wait-ForLaunchedServiceOwnership` требует точного равенства, поэтому старт бэкенда падает с «port is owned by a different process».

**Два honest варианта, выбор за владельцем:** пересоздать `.venv` с `--symlinks`, чтобы `python.exe` был настоящим интерпретатором; либо признать дочерний процесс в контракте владения — сопоставлять по дереву процессов, а не по равенству PID. Второе надёжнее (не зависит от того, как собран venv у следующего разработчика), но трогает `Get-ListeningPid` / `Wait-ForLaunchedServiceOwnership`, то есть механизм владения, который сам по себе защищает от убийства чужого процесса.

### 2026-09-23 — где живёт переносной PostgreSQL: вопрос владельцу, не агенту

**Получатель: владелец.** Решения нет, есть обоснованное предложение.

`docs/ru/backend/postgres-local-portable.md` ставит кластер в `%USERPROFILE%	ools`, то есть **вне папки проекта**, а решение владельца 2026-09-21 гласит «никаких каталогов вне папки проекта» (`AGENTS.md` §7).

**Предложение исполнителя `T1710` — не менять**, с тремя проверяемыми доводами: правило писалось про **рабочие каталоги задачи** (worktree, клоны ревью, артефакты), которые уезжают вместе с ней, а кластер — инструмент машины, ровесник Node и Python; кластер **общий по построению** — runbook делит его между параллельными агентами, внутри worktree каждый ставил бы свой (~300 МБ и минуты на задачу); `pgdata` — изменяемая БД, а гард `test_p014_t1406_no_mutable_database_in_the_working_tree.py` существует ровно потому, что база в рабочем дереве однажды уже оказалась.

Если владелец решит иначе, менять придётся §1–§3 runbook и путь `$data`, **а не лаунчер** — тот знает только `host:port`.

## Остаток закрывающего ревью стадии 1 программы 017 — 2026-09-23

Ревью Codex на `37fec08..5e687dd` вернуло `CLASS-1-COUNT: 0`, `CLASS-2-COUNT: 13`, `READY-TO-CLOSE: YES`. По §19.5 стадия закрывается, и находки класса 2 **не становятся задачами** программы. Семь из тринадцати исправлены узкой правкой после закрытия, потому что каждая либо регрессия этой же стадии, либо ложное утверждение о деструктивной операции; правка только сужает, новых сущностей не вводит. Пять записаны здесь и **не чинятся**, и причина общая: все пять — про сам механизм проверки, а тринадцать находок подряд о механизме это числовой признак петли §19.5. Строить гарды над гардами — ровно то, что §19.4 велит прекратить.

**Получатель всех пяти — владелец.** Ни одна не двигает деньги и не имеет пути в продукте.

- **F2 — граница сброса доказывает пространство имён, а не владение.** `scripts/dev_database.py` принимает любую базу по шаблону `geov0_dev_*`; простаивающая база соседнего агента проходит так же, как своя. Замер отказа на `geov0_test_p017t1711` доказал отделение от тестового пространства имён, а не защиту соседних dev-баз. Починка требует метаданных владения — **новой сущности**, то есть это уже не починка, а предложение программы.
- **F6 — `GeneratorExit` на выходе генератора прячет отказ очистки.** Если контекст-менеджер провизионирования обернуть в асинхронный генератор и закрыть его, флаг тела остаётся «упало», и отказ `DROP DATABASE` сводится к предупреждению. Путь достижим только такой обёрткой; в репозитории её нет.
- **F7 — гарды «каждый PR» не смотрят `needs:`.** `needs: container-smoke` на обязательном job'е тихо снял бы его с PR, потому что `container-smoke` только расписанный. Контрпример — мутация; в сегодняшнем workflow такого нет.
- **F8 — у покрытия операций нет независимого знаменателя.** `every_operation_examined` сравнивает операции журнала с операциями, которые увидела сверка, — оба числа из одного источника и могут вместе пропустить одну и ту же команду рецепта. Предсказано чтением, прогоном не подтверждено.
- **F9 — исключение для `quality.yml` в гарде единственного владельца шире фикстуры, которую объясняет.** Исключён весь файл, поэтому вторая копия bootstrap в том же workflow невидима.

**Отдельно — готовность советует сбросить базу, когда не сошлась сверка.** Найдено исполнителем узкой правки (PR #18), вне её объёма и поэтому здесь. Когда `reconciliation_passed` проваливается, лаунчер отказывает стартовать и советует `reset-db`. Но проваленная сверка — это **денежное расхождение**, и сброс уничтожает единственное его свидетельство: журнал, долги и baseline, по которым можно установить, какая операция его внесла. Для демонстрационной базы совет безвреден, для базы, где владелец что-то делал руками, — нет. §9 называет нарушение целостности гейтом, а не поводом начать заново. Решение нужно одно: при `FAILED` сверки лаунчер отказывает **и не советует сброс**, а называет, как сохранить состояние перед разбором. Правка одной строки сообщения, но это решение, а не опечатка.

**Отдельно — репродьюсер к записи о транзите через замороженного участника.** Ревьюер дал его явно, **не исполняя**: активные A, B, C; линии B→A и C→B с лимитом 10 и разрешённым посредничеством; заморозить B; платёж 1 из A в C с `max_hops=2` — роутер путь допускает, статус B не читается. **В класс 1 это не переводит**, и ревьюер сказал почему: протокол `suspended` только именует, запрета транзита в нём нет, а репродьюсер поведения без нормативной посылки не доказывает, что поведение неверно. Запись выше остаётся развилкой из трёх исходов, и первым шагом по ней остаётся решение, а не код.

## Найдено переключением тира на Postgres (017, стадия 2c) — 2026-09-23

### `simulator-super-smoke` теперь красный на старте тира — сознательно

**Получатель: владелец.** Класс 2. Расписанный job на `windows-latest`, где сервиса Postgres нет. До стадии 2c он шёл на SQLite; после неё SQLite-тира не существует, и тир отказывает до сбора тестов (exit 4).

Давать ему Postgres сейчас бессмысленно: он уже заблокирован двумя дефектами, записанными выше 2026-09-21, — переполнением `simulator_runs.seed` в int32 и `SerializationError` без ретрая на пути реального тика. Отключать триггер, чтобы job не краснел, — худший вариант: зелёный job, который ничего не проверяет. **Оставлен красным с названной причиной.** Условие возврата: оба блокера устранены, после чего job получает сервис Postgres и URL.

### В базе тира остаются строки без жертв

После полного прогона в базе тира лежат 5 строк `integrity_audit_log` (модули `p1_*`, `payment_engine_uow_retry`, `payment_engine_audit_conflict`, `unit/test_p015_step5c_reaction_and_hold`) и 3 строки `simulator_runs`. Их сегодня никто не читает, поэтому ни один тест от них не краснеет. Это тот же класс, что RESIDUE стадии 2b, только без жертвы; по §2 без сигнала не чинится. Станет находкой, как только появится тест, который эти таблицы считает.

### Гард `T1525` освобождает теперь любой модуль под `tests/`, а не только 56 маркерных

Раньше исключение гарда опиралось на `pytestmark = pytest.mark.postgres`. После переключения гарантия Postgres — это сам тир, для всех модулей. Расширение обосновано новым инвариантом (тир отказывает без Postgres), и контрпроверка на известных SQLite-модулях зелёная, но **это расширение исключения**, и внешнее ревью стадии 2 должно его проверить.

## Остаток закрывающего ревью стадии 2 программы 017 — 2026-09-23

Ревью Codex на `ea14726..253359b`: `CLASS-1-COUNT: 0`, `READY-TO-CLOSE: YES`. Стадия закрыта по §19.5. Четыре находки из шести исправлены узкой правкой после закрытия; две записаны здесь и не чинятся — обе про сам механизм проверки, а шесть находок подряд о механизме — числовой признак петли §19.5. **Получатель — владелец.** Ни одна не двигает деньги.

- **F2 — расширенное исключение гарда `T1525` даёт ложноотрицательный путь.** После переключения тира на Postgres исключение гарда распространилось с 56 бывших маркерных модулей на все под `tests/`. Функция, признающая модуль «отказывающим не-Postgres», принимает подходящий отказ **где угодно** в модуле и не связывает его с конкретным конструктором движка. Модуль может присвоить `URL = "sqlite+aiosqlite:///:memory:"`, построить `create_async_engine(URL)` без контроля транзакций и держать не связанный с этим Postgres-хелпер со скипом — сканер его пропустит. Починка требует связать отказ с конструктором разбором AST — это гард над гардом. Предсказано чтением, исполнением не подтверждено. Оркестратор сам вынес это расширение ревьюеру, когда принимал его.
- **F6 — посылка о `scratch_db` была неполной, и исправлена в спеке 017.** На SQLite-стендах живут не только тесты механизма SQLite, но и доменные тесты: политика адаптивного клиринга, SSE аудита, аудит после тика. Они проверяют поведение приложения на SQLite. **Для стадии 3 это условие:** прежде чем убрать SQLite из `scratch_db`, эти тесты обязаны переехать на Postgres, иначе их покрытие умрёт вместе со стендом.

### Ось Windows PowerShell 5.1 не измеряется в CI вовсе

**Получатель — владелец.** Класс 2. Разрыв между локальным Windows (3020 выбранных тестов) и CI на ubuntu (2967) объяснён полностью, все 53 из 53: `tests/unit/test_run_full_stack_database_url_redaction.py` параметризуется по интерпретаторам PowerShell, найденным при сборе (`shutil.which` и `%SystemRoot%`). На Windows их два — pwsh 7 и Windows PowerShell 5.1, на ubuntu один. Доказано исполнением: сбор в чистом worktree дал те же 3020, гипотеза о локальных артефактах опровергнута.

Логика лаунчеров — редакция секретов, владение процессами, откат при старте — **проверяется** в обязательном гейте на Linux под pwsh 7, так что ложного зелёного по логике нет. Не проверяется только работа тех же лаунчеров под Windows PowerShell 5.1: на Linux его нет в принципе, а ни один CI-job не запускает этот модуль на Windows. Директивы `#Requires` в лаунчерах нет, `README.md` показывает запуск без указания редакции. Если поддержка 5.1 обязательна — её сейчас измеряет только локальный прогон на Windows.

## Найдено срезом S1 стадии 3 программы 017 — 2026-09-24

### Закоммиченные демо-фикстуры EUR и HOUR расходятся с генератором — знак учтён дважды

**Получатель: владелец.** Класс 2: демо-данные Simulator UI, не денежный путь. Найдено исполнителем S1 попутно.

Регенерация EUR и HOUR даже на старом коде даёт файлы, отличные от закоммиченных. Пример: в `EUR/snapshot.json` закоммичено `"net_balance_atoms": "-7250"`, генератор пишет `"7250"` — оба при `net_sign: -1`. Если знак несёт `net_sign`, то `net_balance_atoms` — модуль, и верен генератор, а закоммиченный файл учитывает знак дважды.

**Почему это не было видно:** `prebuild` сборки Simulator UI v2 перегенерирует только UAH, поэтому EUR и HOUR никогда не сверялись с генератором. Файлы не правились, причина не разобрана — нужно установить, какая сторона верна, прежде чем перегенерировать.

## Принятые риски — датированные решения владельца

### 2026-09-11 — программа 013: принят риск и программа закрыта

**Маркеры:** `VERDICT-013-CLOSURE-RISK: ACCEPT`, `VERDICT-BLOCKER: NONE`,
`VERDICT-RECORD-SCOPE: c80f600..1bd4f41`, `VERDICT-CONVERGING: YES`.

**Кто принял.** Решение внешнего делегированного держателя (Codex `codex-cli 0.154.0`,
`-m gpt-5.6-sol`, `model_reasoning_effort=high`, `--sandbox read-only`, замороженный клон на
`1bd4f41`, exit `0`), по тому же делегированию непродуктовых решений, что и обе записи по 012 ниже
(файл ведётся от новых к старым).
Держатель — **не тот, кто находил**: находки `T1309` дал `gpt-6-astra`.

**Он отказывал четыре раза, и каждый отказ находил настоящее:** отсутствие перекрёстного ревью
финальной дельты; несогласованность записи о гейтах; отозванную цифру, уцелевшую в комментарии,
дважды объявленном исправленным; и строку реестра, говорившую «отказывал дважды», когда отказов
было больше. Принято только после того, как все четыре закрыты.

**Область — `c80f600..1bd4f41`.** Формулировка исключения дана держателем и воспроизводится по
существу дословно:

> `1ca51ca` находится в предках `c80f600..1bd4f41`, но меняет только программу 014
> (`specs/014-false-green-guards/spec.md`). Он не входит в содержательную дельту программы 013;
> настоящее решение программу 014 не рассматривает и риска по ней не принимает. В двухточечной
> истории он виден лишь потому, что диапазон выражает предков, а не принадлежность к программе.

**Принимаемые ограничения — именно те, что назвал держатель:**

- Независимое внешнее ревью покрыло `c80f600..95f5efe`, вернуло `MECHANISMS: BROKEN` и
  `READY-TO-CLOSE: NO` и **не запускало ни одного гейта**; все приведённые цифры гейтов — локальное
  evidence.
- Шесть находок внешнего ревью исправлены **после** него, и фикс-дельта внешне не проверялась:
  разрешённый §15 круг исчерпан.
- Перекрёстное ревью `95f5efe..a531b49` — **внутреннее фоллбэк-evidence, а не §15 независимость**.
  Семь находок: шесть закрыты, седьмая (`P3`, политика строгого декодера) **не решена и не имеет
  получателя** — запись выше в этом же файле.
- **Принимаемый остаточный риск:** внешне непроверенные финальные правки могут содержать регрессию
  того же класса, пропущенного потребителя, ложный ноль/зелёное/«неизвестно», смещённую выборку,
  вхолостую проходящую мутационную проверку или неточное evidence. Это **наблюдение, а не
  предположение**: шесть находок из первых пятнадцати внесены собственными починками программы, плюс
  отдельно найденный вакуумный тест.
- **Локальное evidence на финальном по поведению дереве:** канонический бэкенд
  `1871 passed, 2 skipped, 137 deselected`; Admin UI `383 passed`; Simulator UI `1069 passed`;
  сборка и lint админки, typecheck симулятора — пройдены. Postgres `133 passed` получен **голым
  `pytest`**, то есть debug-путём, а не каноническим Postgres-гейтом.
- **Ни один полный Playwright-набор не запускался.** CI дал только две smoke-команды внутри одной
  smoke-джобы. Продакшен-сборка симулятора локально не запускалась, но покрыта джобой
  `Required local-equivalent gates`.
- **CI зафиксирован на `7a808eb`** (финальное по поведению дерево) **и `0dd37f6`** (потомок,
  сохраняющий поведение), **а не на точной голове `1bd4f41`**.
- Файловая система ревьюера **не была credential-free** — credential-free только переданный клон;
  постоянный риск машины принят записью 2026-08-14.

**Чего эта запись НЕ утверждает:** ни `CLEAN`, ни `READY-TO-CLOSE: YES`, ни того, что внешнее ревью
пройдено; ни внешней проверки финальных правок, ни внешнего воспроизведения какого-либо гейта; ни
канонического Postgres-результата; ни полного покрытия Playwright/E2E; ни CI на точной голове
`1bd4f41` и ни «весь CI зелёный»; ни credential-free файловой системы и ни покрытия всего
репозитория; ни того, что нерешённая `P3` починена или кому-то назначена; ни того, что принятие
риска доказывает корректность; ни того, что `1ca51ca` или программа 014 чем-либо покрыты. «CI
запускает одну джобу» — неточно: **одну smoke-джобу, содержащую две smoke-команды**. И не
«продакшен- и тестовый код не менялся», а **поведение не менялось**: отслеживаемые правки
комментариев в `.ts` были.

**Область действия:** снимает требование §15 **только для программы 013** и не создаёт исключения ни
для одной последующей.

### 2026-09-10 — программа 012: принят риск дельты `T1212`/`T1213`/`T1214` и программа закрыта

**Маркер:** `VERDICT-012-CLOSURE-RISK: ACCEPT`, `VERDICT-BLOCKER: NONE`,
`VERDICT-RECORD-SCOPE: 422ed50^..3aa6b83`.

**Кто принял.** Решение внешнего делегированного держателя (Codex `codex-cli 0.154.0`,
`-m gpt-5.6-sol`, `model_reasoning_effort=high`, `--sandbox read-only`, замороженный клон на
`3aa6b83`, exit `0`), по тому же делегированию непродуктовых решений, по которому принята запись от
2026-08-25 выше. Держатель — **не тот**, кто находил: находки `T1214` дал `gpt-6-astra`, подпись
ставила другая модель. Владелец в тот же день делегировал оркестратору продуктовую развилку этой же
волны словами «выбери оптимальный вариант».

**Что именно принято — диапазон `422ed50^..3aa6b83`**, поимённо: `422ed50` (сужение
`Equivalent.precision` до `0..8`), `afb099d` (фикс-раунд `T1213`), `bee9ca3` (решение по развилке и
снятие переоценок), `c55feaa` (сверка плана исполнением, правки 015), `3aa6b83` (фикс-раунд `T1214`).
**Итоговый снимок — `ff619d7` на `main`**, его дерево совпадает с `3aa6b83`.

**История ревью, без сглаживания.** `T1213` ревьюил `422ed50`. `T1214` ревьюил `422ed50..c55feaa` и
вернул пять `P2` плюс `VERDICT-012-CLOSURE: RISK-ACCEPT`, **явно оговорив, что его рекомендация
принятием риска не является**. Пять находок исправлены в `3aa6b83`, и **`3aa6b83` внешне не
проверялся**: единственный разрешённый §15 круг по fix-delta израсходован. Оба прежних круга
(`T1211` и повторный срез) закончились `READY-TO-CLOSE: NO`; эти вердикты остаются привязанными к
своим ревизиям и задним числом не пересматриваются.

**Принимаемый остаточный риск, названный прямо:** финальные исправления могут содержать регрессию,
обход защиты, неполный домен выборки или ложно-зелёный проверочный механизм. Основание считать это
приемлемым — не «ревью прошло», а то, что ни одна находка не установила действующего продакшен-`P1`,
а остаток является **ограниченным долгом верификации**.

**Восемь названных ограничений** (полный текст — часть 5 `## Verification plan` программы 012):

1. `DEFAULT_MAX_AMOUNT_SCALE` не охраняется ничем.
2. Allowlist float-гарда сверяется по имени идентификатора и сканирует шесть модулей.
3. Половина `E+` инварианта об экспоненте слабее половины `E-`.
4. `admin-ui/src/utils/decimal.test.ts` не различает дефект форматтера.
5. Шесть живых сайтов `Decimal("0.01")` — `P3`-остаток `T1203`.
6. Оба Playwright-набора и сборка `simulator-ui` не прогнаны; `admin-ui build` и
   `simulator-ui typecheck` прогнаны.
7. Исторический диапазон таймингов из пяти прогонов сохранённым артефактом не подкреплён.
8. ~~CI на итоговом снимке `ff619d7` из агентской сессии не проверен.~~ **Снято в тот же день,
   после подписи:** владелец подтвердил разрешение на чтение токена, прогон прочитан.
   `b1fbd30` (снимок сместился на два слияния позже — оба чисто документационные, кодовый диапазон
   принятия не менялся), run `34496260923`, `success`; зелены `Required local-equivalent gates`,
   `Python static diagnostics`, `Active UI Chromium smoke`, `Development image content policy`.
   **Пять джоб пропущены как `scheduled/manual`**, включая `PostgreSQL integration` — Postgres-тир
   в CI не исполнялся, и по нему остаётся только локальное evidence. Снятие пункта **не расширяет
   принятие риска**: остальные семь ограничений в силе.

**Локальное evidence после последней правки** — и это **локальные результаты оркестратора, а не
воспроизведённые внешне**: бэкенд `1865 passed, 1 failed` (падение — известный `F-014-11`, предмет
программы 014), Postgres `133 passed`, `admin-ui` `308 passed`, `simulator-ui` `995 passed`, сборка
`admin-ui` зелёная.

**Постоянное ограничение окружения:** credential store в домашнем каталоге ревьюеру достижим; риск
принят 2026-08-14 записью ниже. «Credential-free» относится к переданному клону, а не к файловой
системе.

**Чего эта запись НЕ утверждает:** ни `CLEAN`, ни `READY-TO-CLOSE: YES`, ни внешней проверки гейтов,
ни успешного CI, ни сплошного покрытия репозитория, ни Playwright/сборок, которых не было, ни полного
покрытия всех денежных путей, ни credential-free файловой системы, ни того, что ревьюер сам принял
риск. И она **не повторяет** утверждение «после `8b04e68` продакшен- и тестовый код не менялся» —
именно его неверность и потребовала новой записи.

**Область действия:** снимает требование §15 **только для программы 012** и не создаёт исключения ни
для одной последующей.

### 2026-08-25 — программа 012: принят риск финальной externally-unverified дельты

**Решение принято внешним держателем процессной развилки** (Codex `gpt-5.6-sol` high, read-only,
exit `0`, маркер `VERDICT-012-CLOSURE: RISK-ACCEPT`), которому владелец делегировал непродуктовые
решения. Прецедент делегирования — запись 011 ниже. Основание в правилах — `AGENTS.md` §15:
закрытие через явно и датированно принятый риск с записью здесь.

**Что именно принято.** Оба внешних среза кончились `VERDICT-READY-TO-CLOSE: NO`: круг 1 на
`dd1218d` (`FINDINGS` / `SAMPLE-BIAS: FOUND`), круг 2 по fix-delta на `a8257a1` (`FINDINGS` /
**`SENTINELS: WEAK`**). Единственный разрешённый §15 круг израсходован на круг 2. Все находки
закрыты, но **закрытие находок круга 2 внешне не проверялось**. Внешне непроверенными остаются семь
коммитов — проверено, что ни один не является предком ревьюированного `a8257a1`
(`git merge-base --is-ancestor` даёт exit `1` для каждого):

`e5a199c`, `9e8152e`, `ed9bda3`, `9f51763`, `1c2e3aa`, `da548ad`, `8f35f08`.

**Содержание риска, названное прямо:** в исправлениях второго круга может остаться новый обход,
регрессия либо ложнозелёный измеритель; внешний ревьюер их не проверял и гейты не покрывал.

**Что сделано вместо третьего круга, и это не замена.** Каждый из семи прошёл перекрёстное
внутреннее ревью другой сессией по чужой поверхности, поимённо в
`specs/012-money-precision-and-representation/evidence/t1211-slice2-ledger.txt`. Ревьюеры — сессии
той же модели, поэтому независимость §15 не восстановлена.

**Отдельное ограничение evidence.** Внешний ревьюер работал на read-only ФС и **не запускал ни
канонический раннер, ни тесты с БД**; UI гонялся через `node_modules` соседнего checkout. Гейты
внешним ревью не покрыты вовсе; единственное gate-evidence — локальные прогоны на `8b04e68`
(PostgreSQL `120 passed`, оба UI, default-тир с отдельно воспроизведённым baseline-падением
`F-014-11`). После `8b04e68` продакшен- и тестовый код не менялся.

**Чего это решение НЕ означает.** Оно снимает требование внешнего ревью **только для закрытия
программы 012**, не доказывает корректность дельты и не создаёт исключения для последующих программ.
Дельта остаётся явно `UNVERIFIED`. Отдельно зафиксировано: `READY-TO-CLOSE: NO` относится к
замороженным `dd1218d` и `a8257a1` и дефекта текущего HEAD не доказывает — внешний ревьюер является
источником находок, а не органом окончательного решения.

### 2026-08-24 — программа 011: принят риск непроверенного второго fix-delta

**Решение владельца, принятое через постоянное делегирование процессных развилок внешнему
ревьюеру** (тот же порядок, что для `T1101`, `T1103b`, `F-011-7`, `F-011-9`; владелец в сессии
2026-08-24 прямо указал советоваться с ним и довести волну до конца). Вердикт ревьюера —
`VERDICT-PROCESS: A`.

**Суть.** Обязательное внешнее ревью программы 011 не доведено до вердикта `CLEAN`. Первый круг на
`6a5bfe8` вернул `FINDINGS` (три находки). Единственный разрешённый §15 круг **только по fix-delta**
на `62b018a` также вернул `FINDINGS` (две находки), причём одна из них — дефект, внесённый
*исправлением первого круга*. Исправления, входящие в итоговый диапазон и **внешним ревью не
проверенные**: `016a8b4`, `7bf20ff`, `758faf8`. Дополнительный круг не запускается: §15 говорит
«допускается один review только fix-delta» — в единственном числе, без оговорки «на каждый круг с
находками», и следом прямо запрещает бесконечный цикл. Третий круг был бы процессным исключением,
которого §15 не предусматривает.

**Что именно принимается.** Риск того, что последнее исправление gate-а по media type и allowlist и
связанная правка `api/openapi.yaml` содержат новый обход, fail-open либо расхождение канона и
runtime. Эмпирика волны показывает, что риск не теоретический: ровно это уже случилось один раз.

**Чего это решение НЕ означает.** Оно снимает требование внешнего ревью **только для закрытия
программы 011**, не является доказательством корректности дельты и не создаёт исключения для
последующих программ. Дельта остаётся явно `UNVERIFIED`.

**Что сделано вместо третьего круга, и это не замена.** Самое слабое место второй находки закрыто
измерением: JSON-артефакты теперь скачиваются через производственный маршрут, поэтому четыре ветви
канона проверяются на настоящих телах, а не транскрибированы (`758faf8`, проверено мутацией). Это
усиливает дельту, но внешним ревью не является.

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

## Остаток закрывающего ревью стадии 3 программы 017 — 2026-09-24

Класс 2 по §19.5; программу 017 не держит. Получатель — следующая работа, трогающая соответствующий файл.

- ~~**Гард `T1707` не видит `getattr(obj, "dialect")`.** `tests/unit/test_p017_no_second_dialect.py:252` распознаёт чтение диалекта только как `ast.Attribute`; обработчик вызовов (`:259`) литеральный `getattr` не разбирает. Воспроизведено 2026-09-24: `scan_source` на `if not getattr(engine, "dialect").name.startswith("postgres"): return` → `{}`. Это шире заявленных слепых пятен (`:65`: имена, собранные в рантайме). Лечение — либо распознавать `getattr` с литеральным вторым аргументом, либо назвать это слепым пятном в сообщении гарда. P2.~~ — закрыто 2026-09-24, коммит `chore(017): close the stage-3 review remainder` (ветка `claude/017-polish`): гард читает `getattr(<expr>, "<литерал>")` как `<expr>.<литерал>`; `getattr` с нелитеральным именем назван слепым пятном в сообщении гарда.
- ~~**Тест формы записи журнала может пройти на чужом CHECK.** `tests/unit/test_p015_b4a_journal_mechanism.py:1226` обещает краснеть при снятии `chk_debt_journal_entries_delta`, но вход `before=2, after=3, delta=0` отвергает и арифметический CHECK, а ассерт `:1274` принимает любой CHECK. Точное имя ограничения держат `tests/integration/test_p015_b4_entries_and_money_postgres.py:2426,2563`, так что эффект не потерян. P3.~~ — закрыто 2026-09-24, коммит `chore(017): close the stage-3 review remainder` (ветка `claude/017-polish`): каждая подделка утверждает имя ограничения, которое PostgreSQL сообщает (проверяет CHECK по алфавиту имён); снятие `chk_debt_journal_entries_delta` из миграции 022 краснит тест (измерено).
- ~~**Замер «250 015 значений» в докстринге `app/core/ledger/journal.py:489` невоспроизводим из дерева:** пробы, популяции и команды в репозитории нет. Удаление `MONEY_ROUND_TRIP` от этого не становится небезопасным — точное хранение держат `test_p015_b4a_journal_mechanism.py:970` и `test_p015_b4a_journal_postgres.py:86`. P3.~~ — закрыто 2026-09-24, коммит `chore(017): close the stage-3 review remainder` (ветка `claude/017-polish`): докстринг заменён проверяемым утверждением о `AsyncpgNumeric` и ссылками на два теста точного хранения.
- ~~**Проза в настоящем времени о SQLite** осталась в `app/api/v1/admin.py:1332,1431,1551`, `app/core/clearing/service.py:97,909,972`, `app/core/ledger/reconciliation.py:965,1134`, `app/core/simulator/inject_executor.py:44`, `app/db/models/simulator_storage.py:14,68,96,123`, `app/schemas/equivalents.py:28`, `app/schemas/trustline.py:28`, `scripts/cleanup_simulator_runs.py:88` и в части `docs/ru` (`runbook-dev-wsl2…:293`, `network-economy-analyzer-spec.md`, `simulator/scenarios-and-engine.md:37,468,575`). Поведения не меняет; правится попутно при следующем касании файла.~~ — закрыто 2026-09-24, коммит `chore(017): close the stage-3 review remainder` (ветка `claude/017-polish`): перечисленные места приведены к текущему коду.
- **Половина `T1541` — bootstrap entrypoint'а — не тронута** (записано при закрытии стадии 2): префлайт `docker-entrypoint.sh` строит схему отдельно от тира; число прогонов `upgrade head` за сессию не мерено.
- **Замер времени CI с промахом кэша зависимостей** не сделан — за стадию промаха не случилось (бюджет `T1706`).
