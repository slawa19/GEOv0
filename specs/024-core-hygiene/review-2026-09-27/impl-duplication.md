# impl-duplication — весь `app/` (дублирование кода и политики)
HEAD: 4119ace. Прочитано целиком: `app/core/integrity.py` (151), `app/api/v1/integrity.py` (319), `app/core/trustlines/service.py` (791), `app/core/balance/service.py` (296), `app/core/simulator/net_balance_utils.py` (99), `app/core/simulator/viz_rules.py` (123), `app/core/simulator/real_tick_metrics.py` (137), `app/core/simulator/real_debt_snapshot_loader.py` (83), `app/core/auth/crypto.py` (50), `app/api/v1/websocket.py` (96), `app/utils/metrics.py` (60), `app/db/models/transaction.py`, `app/core/simulator/commit_resolution.py:1-80`.
Прочитано фрагментами по кандидатам (не целиком): `app/api/v1/admin.py` (90-450, 560-880, 1030-1110, 1520-2240), `app/core/payments/service.py` (40-320, 560-910, 1150-1290, 1700-1860, 1895-2045, 2250-2262, 2690-2760, 2780-2790), `app/core/clearing/service.py` (185-240, 345-415, 450-480, 640-725, 820-850, 2025-2045, 2140-2190, 2340-2400), `app/core/payments/router.py` (170-300, 440-446), `app/core/admin/metrics.py` (36-60, 360-510, 560-715), `app/core/invariants.py` (15-90, 230-288), `app/core/money_boundary.py` (334-410), `app/api/deps.py` (120-345), `app/core/auth/service.py` (20-155), `app/core/participants/service.py` (25-60, 165-180), `app/core/simulator/{snapshot_builder,viz_patch_helper,edge_patch_builder,inject_executor,real_clearing_engine,money_replay,real_runner_impl,real_payment_planner,metrics_bottlenecks}.py` (по кандидатам), `app/schemas/{admin,equivalents,integrity,trustline}.py` (валидаторы дат).
Не прочитано целиком: остальное `app/` — в т.ч. `app/api/v1/simulator.py` (3053, почти весь в скоупе 021), `app/core/ledger/{book,reconciliation}.py`, `app/main.py`, `app/db/journal_*`.

Инструменты (скрипты в `scratchpad/review/dup/`): `clones.py` / `clones3.py` — AST-нормализация (имена → плейсхолдеры, строки → `S`, docstring выброшены), точные совпадения окон из 5 (и 3) инструкций ≥ 8 (≥ 7) строк + Jaccard по шинглам листовых инструкций на уровне функций; `handlers.py` — группировка одинаковых тел `except`; `schemas.py` — классы с одинаковым набором аннотированных полей; `settings_defaults.py` — сверка `getattr(settings, NAME, default)` с дефолтами `Settings`. Кандидаты прочитаны глазами; тривиальные (импорты модулей, DDL-строки `journal_*`, `action_*` симулятора — скоуп 021, root/versioned health и list/search участников — non-goals 016, детектор циклов triangles/quadrangles — скоуп 023) отброшены.

## Summary

- Из восьми находок 016 **живы шесть** (F-016-1, -2, -3, -4, -6, -7), **одна практически исчезла** (F-016-5: вместе с `recovery.py`/`PREPARED` пропал сам stale-предикат; остался недостижимый набор «in-progress» в `payments/service.py:881-897`), **одна жива частично** (F-016-8: mock уходит с 022, бэкенд и fallback живы). F-016-3 после 019 не упростился: извлекателей SQLSTATE теперь **пять**, и трастлайны до сих пор ходят по `__context__`, который платёж и клиринг сознательно исключили.
- Главное новое: **нетто-позиция и её «атомы» считаются в 8+ местах**, и админский граф (`admin.py:1662-1664`, `:2030-2031`) воспроизводит ровно тот дефект `RT-012-2`/T1210-6, который 012 починила в `net_decimal_to_atoms`: суб-квантовый долг получает `net_sign: 0`. Правила отрисовки скопированы четырежды; 022 удаляет три симуляторных модуля, но **админские копии в `admin.py` в её скоупе не названы**.
- `int(precision or 2)` в пяти местах симулятора превращает законную точность `0` в `2` — копия, разошедшаяся с доменом (`balance/service.py` читает `int(equivalent.precision)`).
- «Довести задачу до терминального состояния под отменой вызывающего» (`asyncio.shield`-цикл) реализовано **пять раз** с разной политикой повторной отмены; дефект «потерянный импульс отмены» чинили в одной копии (020, `40bbbdc`), остальные не тронуты.
- Ёмкость ребра — три копии (router, `_segment_capacity`, `balance`) с **разным множеством рёбер**: баланс и привязка платежа засчитывают ёмкость из встречного долга без активной линии, роутер такого ребра не строит (INFERENCE, не воспроизведено); все три при этом расходятся с формулой протокола §6.3.1.
- «JWT → активный участник» трижды в `deps.py`, а четвёртая копия (`websocket.py:33-40`) статус участника не проверяет вовсе.
- Первым бы делал N-1 (атомы админского графа) и N-3 (`precision or 2`) — обе S, обе закрывают расхождение факта; затем N-6 (WS). N-4 и N-5 требуют сначала записанного решения, а не свода копий.

## Часть 1 — статус F-016-1…8 на `4119ace`

| F-016 | Статус | Якоря на HEAD | Что изменилось |
|---|---|---|---|
| F-016-1 integrity suite, двойной прогон `/verify` | **жива** | `app/api/v1/integrity.py:87-115` (`/status`), `:191-219` (`/verify`), `app/core/integrity.py:92-119`; повторный прогон — `integrity.py:231-234` | `zero_sum` снят (T1402), «шесть сканов» стали четырьмя на эквивалент (`trust_limits` + `debt_symmetry` дважды). Тела `/status` и `/verify` — точный клон по AST (окно `:85-115` ≡ `:189-219`), с трижды повторённым абзацем про `zero_sum` (`integrity.py:80-84`, `:184-188`, `core/integrity.py:79-88`). `except Exception: pass` на `:258-259` известен (BACKLOG «Проглатывание исключений», оценён как in-memory) |
| F-016-2 materialization `IntegrityAuditLog` | **жива, якоря сдвинулись** | trustlines `service.py:244-268`, `:377-425`, `:493-540`; платёж — теперь `app/core/payments/service.py:2011-2045` (engine удалён 019); клиринг `clearing/service.py:2141-2190`; verify `integrity.py:231-259`; седьмая, не названная 016 форма — `simulator/real_tick_orchestrator.py:575-600` (`SIMULATOR_AUDIT_DRIFT`, скоуп 021) | Семантика отказа по-прежнему разная: трастлайн fail-closed, платёж глотает не-DBAPI ошибку с `warning` (`payments/service.py:2039-2044`), клиринг — `warning` и `checkpoint_after=None` (`:2148-2155`). В update/close трастлайна `eq_code` читается дважды подряд (`:401-407` и `:427-431`; `:516-522` и `:542-546`) |
| F-016-3 SQLSTATE / цепочка исключений | **жива, изменилась** | payments `service.py:62-114` (`orig`/`__cause__`, без `__context__`, wrapper `.code` пропускается); clearing `service.py:349-392` (та же цепочка, но **wrapper `.code` засчитывается**); trustlines `service.py:61-67` (**`__cause__ or __context__`**); симулятор `money_replay.py:133-138` (только непосредственный `orig`, включая `.code`); `real_runner_impl.py:69-76` (только `orig`, без `.code`) | Приватный `engine._get_pgcode()` исчез с engine — расхождение `:471`/`:475` снято. Но правило «никогда не `__context__`» (`payments/service.py:71-83`) в `trustlines/service.py:67` не соблюдается (N-7). Множество `{40001, 40P01}` объявлено 4 раза: `payments/service.py:54`, `money_replay.py:89`, `real_runner_impl.py:65` (+`55P03`), инлайн `clearing/service.py:413`, `:1509` |
| F-016-4 `list_all/count_all` | **жива** | `trustlines/service.py:628-654` и `:683-711`; пара вызовов `admin.py:1538-1552` | Без изменений. Соседний `get_by_participant` (`:578-585`) при неизвестном коде эквивалента валидирует код и бросает 404, `list_all` не валидирует и молча отдаёт `[]` — третий вариант той же политики разрешения фильтра |
| F-016-5 active/stale payment policy | **исчезла (остаток — недостижимый код)** | `recovery.py` удалён; admin-читатели «stuck» отвечают пусто без чтения (`admin.py:115-121`, `:1038-1050`, `metrics.py:640-642`) | Остаток: `payments/service.py:881-897` проверяет `existing_tx.state in {NEW, ROUTED, PREPARE_IN_PROGRESS, PREPARED, PROPOSED, WAITING}`, но `:778` уже требует `type == "PAYMENT"`, а `chk_transaction_payment_terminal` (миграция 030, `models/transaction.py:31`) запрещает PAYMENT вне `COMMITTED/ABORTED` → ветка недостижима. Это остаток 019 (для dead-code ревьюера), а не дублирование |
| F-016-6 две Zod-схемы AuditLog | **жива** (вне зоны `app/`, проверены якоря) | `admin-ui/src/api/adminContracts.ts:95-111` (`uuid`, `datetime`, `.strict()`) против `admin-ui/src/api/realApi.ts:152-167` (`z.string()`, `.passthrough()`); `admin-ui/src/pages/graph/graphTypes.ts:50-63` (обязательные `object_type/object_id`, нет `user_agent`) | Без изменений |
| F-016-7 обходы `extractErrorMessage` | **жива** (вне зоны) | `useSimulatorApp.ts:77`, `useSimulatorRealMode.ts:113`, `useCookieSessionBootstrap.ts:20-21`, `useSceneState.ts:83-86`, `useInteractActions.ts:104` | Без изменений |
| F-016-8 activity тремя алгоритмами | **жива частично** | бэкенд `app/core/admin/metrics.py:560-715`; fallback `admin-ui/src/composables/useGraphAnalytics.ts:634-690` | `mockApi.participantMetrics` (`mockApi.ts:498`) удаляется вместе с `mockApi.ts` программой 022 — эта треть покрыта. Бэкенд/fallback расходятся по-прежнему; `incident_count` в бэкенде теперь всегда нули (compat до П4) |

## Findings (новые)

| ID | Sev | Category | path:line | Одной строкой | Covered-by | Effort |
|---|---|---|---|---|---|---|
| N-1 | P2 | duplication | `app/api/v1/admin.py:1662-1664`, `:2030-2031` | Атомы нетто-позиции в админском графе — копия без исправления 012/T1210-6: суб-квантовый долг даёт `net_sign 0` | BACKLOG:«Отложено из 015 при задании закрытия — 2026-09-14» (T1521) — **неполно** | S |
| N-2 | P2 | duplication | `admin.py:1681-1743` ≡ `:2050-2109`; `viz_patch_helper.py:143-231`; `viz_rules.py:56-112`; `snapshot_builder.py:417-424` | Правила viz (перцентиль, debt-bin, масштаб, цвет по статусу, alpha/width) в 4 копиях + пятая расходящаяся; 022 удаляет 3 модуля симулятора, но не называет копии в `admin.py` | 022 — **неполно** | M |
| N-3 | P2 | logic-bug | `snapshot_builder.py:173`, `viz_patch_helper.py:54`, `edge_patch_builder.py:71`, `inject_executor.py:420`, `real_clearing_engine.py:88` | `int(precision or 2)` превращает законную точность 0 в 2; домен читает `int(equivalent.precision)` | none | S |
| N-4 | P2 | duplication | `payments/service.py:152-177`, `clearing/service.py:189-211`, `:457-477`, `admin.py:414-450`, `simulator/commit_resolution.py:9-39` | «Довести задачу до конца под отменой» — 5 реализаций с разной политикой повторной отмены и собственной отмены задачи | BACKLOG:«Класс 2 из закрывающего ревью программы 019 (`T1910`, часть 5b)» — **неполно** | M |
| N-5 | P2 | duplication | `router.py:280-299`, `payments/service.py:1835-1858`, `balance/service.py:160-209` | Ёмкость ребра — три копии с разным множеством рёбер: без активной встречной линии роутер ребра не строит, привязка платежа и `available_to_spend` встречный долг засчитывают | none | S (после решения) |
| N-6 | P2 | duplication | `api/deps.py:145-161`, `:179-195`, `:318-334`; `api/v1/websocket.py:33-40` | «JWT → активный участник» трижды в `deps.py`; четвёртая копия (WS) статус не проверяет — замороженный/удалённый участник с живым access-токеном получает события | none | S |
| N-7 | P2 | duplication | `trustlines/service.py:59-67` | Обход цепочки исключений в трастлайнах идёт по `__context__`, против правила `payments/service.py:71-83`; комментарий ссылается на клиринг, который так уже не делает | 016 (F-016-3) — не названо явно | S |
| N-8 | P3 | duplication | `invariants.py:237-257`, `money_boundary.py:334-373`, `invariants.py:62-85`, `admin.py:812-835`, `admin/metrics.py:366-391`, `snapshot_builder.py:201-280`, `viz_patch_helper.py:89-102,265-290`, `balance/service.py:146-158` | Нетто-позиция ~9 способами; в денежном ядре две копии одной семантики (N+1 у клиринга, batched у платежа) | 023 частично (клиринг) | S |
| N-9 | P3 | duplication | `trustlines/service.py:750-761`, `admin.py:657-658,778-779,1768,2129`, `admin/metrics.py:500`, `snapshot_builder.py:236-238` | `used/available` линии — 7 копий; сервис даёт `used=0` закрытой линии, SQL-копии берут долг пары; `snapshot_builder` клампит `available` в 0, остальные нет | none | S |
| N-10 | P3 | duplication | `payments/service.py` ×18 (напр. `:196-201`, `:820-825`, `:1158-1163`) | `try: from app.utils.metrics import …; …inc() except Exception: pass` ×18; в клиринге тот же отказ логируется (`clearing/service.py:1114-1117`) | none | S |
| N-11 | P3 | readability | `payments/service.py:2259`, `:2784`; `router.py:443`; `deps.py:212`; `balance/service.py:54` | Дефолты `getattr(settings, NAME, X)` расходятся с `Settings` (1 vs 3, 50 vs 500) и никогда не срабатывают; `BALANCE_SUMMARY_CACHE_MAX_ENTRIES` — не поле `Settings` | none | S |
| N-12 | P3 | duplication | `participants/service.py:53-57,171-175`, `trustlines/service.py:113-114,154-158,323-324,347-351,462-463,472-476`, `payments/service.py:1256-1271`, `auth/service.py:79-84` | Обёртка «проверить подпись и перемаркировать» ×7, правило «`canonical_json` вне `try`» вносилось в каждую копию по месту | none | S |
| N-13 | P3 | dead-code | `schemas/admin.py:108-113,160-165`, `schemas/equivalents.py:26-31`, `schemas/integrity.py:9-12`, `schemas/trustline.py:25-29`, `admin/metrics.py` ×7 | «Приписать UTC наивной метке из БД» в 5 валидаторах и 7 ветках — остаток SQLite (017): все `DateTime`-колонки `timezone=True` | none | S |
| N-14 | P3 | duplication | `admin.py:135-159`, `simulator/real_scenario_seeder.py:113-120` | Словарь UI→DB статуса участника дважды и расходится: `banned` → `[deleted, left]` в фильтре, `deleted` в сидере | 021 (сидер) частично | S |
| N-15 | P3 | duplication | `core/ledger/book.py:248-255`, `payments/service.py:628-644`, `:1956-1962` | `DeclaredFlow` — поле-в-поле копия `PaymentFlow` + `as_intent()` | none | S |
| N-16 | P3 | logic-bug | `admin.py:812-835` | `/admin/liquidity/summary` без `equivalent` суммирует нетто по всем эквивалентам вместе (INFERENCE) | none | S |

## Детали

### N-1 — атомы нетто-позиции в админском графе без исправления 012
Evidence:
```python
# app/api/v1/admin.py:1662-1664 (snapshot); то же на :2030-2031 (ego)
def _to_atoms(amount: Decimal) -> int:
    # amount is Decimal in major units; convert to integer atoms.
    return int((amount * scale10).to_integral_value(rounding=ROUND_HALF_UP))
```
против владельца:
```python
# app/core/simulator/net_balance_utils.py:79-84
atoms = int((net * scale10).to_integral_value(rounding=ROUND_HALF_UP))
if atoms == 0 and net != 0:
    return 1 if net > 0 else -1
```
Почему это важно: 012 зафиксировала инвариант `sign(atoms) == sign(net)` и `atoms == 0 ⇔ net == 0` (`net_balance_utils.py:50-51`) и доказала, что он ничего не сдвигает там, где было верно. В админском графе он не действует: эквивалент `precision: 1`, долг `A→B = 0.04` → `/admin/graph/snapshot?equivalent=…` отдаёт для A и B `net_balance_atoms "0"`, `net_sign 0`, цвет `person` — `RT-012-2`, переживший в соседней копии. Режим округления (`ROUND_HALF_UP` против `ROUND_DOWN` в `admin/metrics.py:44-50`) — продуктовая развилка T1521 в BACKLOG; **знак — нет**, он уже решён 012. Поэтому BACKLOG берёт находку неполно.
Минимальное исправление: в обоих `_attach_net_viz*` вызвать `net_decimal_to_atoms(cre - deb, precision=precision)` (модуль-лист, импортирует только `decimal` и `app.utils.money`) и удалить локальные `_to_atoms`.
Что может сломаться / call-sites: два вызова (`:1670`, `:2039`); UI читает атомы (`admin-ui` graph analytics); тесты графа админки с суб-квантовыми суммами. Репродьюсер — 5 строк на существующей фикстуре эквивалента с `precision=1`.
Covered-by: BACKLOG:«Отложено из 015 при задании закрытия — 2026-09-14» (T1521, режим) — неполно. Contract: no (форма провода та же, меняется значение на суб-квантовом входе).

### N-2 — четыре копии правил отрисовки
Evidence: `admin.py:1681-1715` (`_percentile`, `DEBT_BINS = 9`, `_debt_bin`, `max_scale = 1.90`, `gamma = 0.75`, `_scale_from_pct`) и `:2050-2083` — точный клон по AST; `admin.py:1722-1731` ≡ `:2090-2099` ≡ `viz_patch_helper.py:173-182` ≡ `viz_rules.py:88-99` (`{"suspended","frozen"} → "suspended"`, `left`, `{"deleted","banned"} → "deleted"`, `debt-{bin}`); `viz_patch_helper.py:143-167`, `:194-231` — копии `viz_rules.percentile_rank/scale_from_pct/debt_bin/link_alpha_key/link_width_key` с комментариями «Mirror SnapshotBuilder…». Пятая, расходящаяся форма: `snapshot_builder.py:417-424` для снимка из сценария не знает `frozen/banned` (статусы сценария, которые сидер переводит в `suspended/deleted`, `real_scenario_seeder.py:115-118`) и отдаёт тип вместо `suspended/deleted`.
Почему это важно: 022 (T2204) удаляет «три модуля бэкенда на 717 строк» (= `viz_rules` 123 + `viz_patch_helper` 308 + `edge_patch_builder` 286) и переносит решения об отрисовке в клиент; `AdminGraphParticipant.viz_color_key/viz_size` при этом продолжают вычисляться в `admin.py` в двух копиях — после 022 бэкенд продолжит решать отрисовку, только без общего владельца.
Минимальное исправление: в 022 добавить `admin.py:1611-1743`, `:1982-2109` в owner surface (удалить viz-поля из админского ответа или временно свести обе копии к `viz_rules`). Независимо: snapshot и ego внутри `admin.py` свести в одну приватную функцию — удаление ~100 строк.
Covered-by: 022 — неполно. Contract: yes, если поля уходят с провода (OpenAPI `AdminGraphParticipant`).

### N-3 — `precision or 2` делает точность 0 равной 2
Evidence:
```python
# app/core/simulator/snapshot_builder.py:173
precision = int(getattr(eq, "precision", 2) or 2)
# app/core/simulator/edge_patch_builder.py:71
precision = int(eq_row[1] or 2)
```
также `viz_patch_helper.py:54`, `inject_executor.py:420`, `real_clearing_engine.py:88`. Точность `0` законна: `schemas/equivalents.py:42` `Field(ge=0, le=8)`, колонка `NOT NULL` (`models/equivalent.py:14`), `api/money-rendering-conformance.json` держит случаи с точностью 0. Домен читает без подмены: `balance/service.py:131`, `:254`; админка — `admin.py:1606`.
Почему это важно: для эквивалента с `precision: 0` симулятор рендерит `"5.00"` вместо `"5"` в снапшоте, SSE-патчах рёбер и в `limit` инжекта (`inject_executor.py:707`, `:843`), а атомы считает в сотых — один участник показывается по-разному админкой и симулятором. Денег не двигает, но это расхождение факта, которое 012 закрывала conformance-таблицей: здесь таблица обходится до входа в `to_money_str`.
Минимальное исправление: `int(x)` без `or 2` там, где значение пришло из колонки `NOT NULL`; фолбэк `2` оставить только для «эквивалент не найден» (`real_clearing_engine.py:83-90`, `inject_executor.py:425`), как у `to_money_str` (`utils/money.py:58-61`).
Что может сломаться: демо-фикстуры симулятора, если в них есть эквивалент с точностью 0 (регенерация по §10). Covered-by: none (021 владеет `inject_executor`/`real_clearing_engine`, но находку не называет; `snapshot_builder`/`viz_*` удаляет 022). Contract: no.

### N-4 — пять реализаций «дождаться терминального состояния под отменой»
Evidence:
```python
# app/core/clearing/service.py:200-205 — собственная отмена задачи не отличается от отмены вызывающего
while not task.done():
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError as exc:
        if caller_cancellation is None:
            caller_cancellation = exc
```
```python
# app/core/simulator/commit_resolution.py:27-33 — отличает, и на повторной отмене отменяет задачу
if task.done():
    continue
current = asyncio.current_task()
if current is not None and current.cancelling():
    if cancellation is not None:
        task.cancel("repeated caller cancellation")
```
Остальные: `payments/service.py:152-177` (проверяет `task.done()`, не проверяет `cancelling()`, повторную отмену пережидает — «must not interrupt»), `clearing/service.py:457-477` (`_commit_to_terminal` — инлайн-копия `_drain_task` того же файла), `admin.py:414-450` (как `commit_resolution`: `cancelling()` + отмена задачи на повторе).
Почему это важно: вопросы «что делать на второй отмене» и «чья это отмена» решены противоположно в соседних денежных путях, и класс дефекта уже проявился: BACKLOG «Класс 2 из закрывающего ревью программы 019» фиксирует потерю импульса отмены в `clearing._drain_task`, починенную параметром `surface_result` только для блока разрешения коммита, с остатком у трёх вызывающих. Копии в платеже и админке в той записи не фигурируют.
Минимальное исправление: не обобщать. Внутри `clearing/service.py` заменить `_commit_to_terminal` на `_drain_task(commit_task, surface_result=False)` (параметр уже есть). Отдельно — датированным решением записать, какая политика повторной отмены верна для денежных путей (пережидать или отменять); после этого у каждого файла одна копия. Общий модуль — только если решение окажется одинаковым.
Covered-by: BACKLOG:«Класс 2 из закрывающего ревью программы 019 (`T1910`, часть 5b)» — неполно; её получатель (020) закрыт 2026-09-26 суженным, остаток без живого получателя. Contract: no.

### N-5 — ёмкость ребра: три владельца, разное множество рёбер
Evidence:
```python
# app/core/payments/router.py:280-292 — ребро debtor→creditor только при активной линии creditor→debtor (:197-202)
for tl in trustlines:
    creditor_id = tl.from_participant_id  # trusts
    debtor_id = tl.to_participant_id      # can owe
    ...
    cap = (limit - debt_debtor_owes_creditor) + debt_creditor_owes_debtor
```
```python
# app/core/payments/service.py:1856-1858 — без активной линии limit=0, встречный долг засчитывается
limit = line if line is not None else Decimal("0")
...
return limit - sender_owes + receiver_owes
```
```python
# app/core/balance/service.py:190-202 — «Debts without trustlines still contribute positive capacity with Limit=0»
if (peer, code) not in processed_spend:
    cap = Decimal('0') - d_me_peer + d_peer_me
    if cap > 0:
        get_eq_entry(code)['spend_capacity'] += cap
```
Почему это важно (INFERENCE, не воспроизведено): обычная односторонняя линия `S→R` (S доверяет R), R должен S `10`, линии `R→S` нет. `GET /balance` для S покажет в `available_to_spend` эти `10`, `_segment_capacity(S→R)` вернёт `10`, а роутер ребра `S→R` не построит — платёж S→R получит «нет маршрута». Комментарий `router.py:297-299` («Debt-only capacity is already included») для этого случая неверен. Протокол (`docs/ru/02-protocol-spec.md:461`, `available_credit(A→B) = limit(A→B) − debt[B→A]`) встречного долга не прибавляет вовсе — три копии расходятся не только между собой, но и с §6.3.1. Какое поведение верно — вопрос протокола; дублирование делает расхождение невидимым.
Минимальное исправление: сначала записать решение (есть ли ребро без встречной линии и прибавляется ли встречный долг), затем одна чистая функция ёмкости в `app/core/payments/`, вызываемая из трёх мест; комментарий роутера исправить в любом случае.
Covered-by: none. Contract: возможно (семантика `available_to_spend`, протокол §6.3.1) — вынести как вопрос протокола.

### N-6 — «токен → активный участник» и WebSocket
Evidence:
```python
# app/api/deps.py:153-161 (и :187-195, :322-328)
result = await db.execute(select(Participant).where(Participant.pid == pid))
participant = result.scalar_one_or_none()
if not participant:
    raise UnauthorizedException("Participant not found")
if participant.status != 'active':
    raise ForbiddenException("Participant account is not active")
```
```python
# app/api/v1/websocket.py:33-40 — ни существования, ни статуса
payload = await decode_token(token, expected_type="access")
if not payload or not payload.get("sub"):
    await websocket.close(code=1008)
    return
pid = str(payload["sub"])
await websocket.accept(subprotocol=_BEARER_SUBPROTOCOL)
```
Почему это важно: `POST /admin/participants/{pid}/freeze|ban` отрезает REST (403 через `get_current_participant`), но WS с живым access-токеном продолжает подписку на события участника (`event_bus.subscribe(pid=…)`, `:73`). `login` (`auth/service.py:70-77`) и `refresh_tokens` (`:131-136`) статус не проверяют, так что токены замороженному участнику выдаются — REST это компенсирует, WS нет.
Минимальное исправление: одна функция `_load_active_participant(db, pid)` в `deps.py`, вызываемая тремя существующими местами и из `websocket.py` (с `close(1008)` вместо исключения). Приоритеты цепочки auth (non-goal 016) не трогать.
Covered-by: none (005 владела `websocket.py` для переноса токена, статус не рассматривался). Contract: no (1008 — уже существующий исход).

### N-7 — трастлайны идут по `__context__`
Evidence: `trustlines/service.py:67` `node = getattr(node, "__cause__", None) or getattr(node, "__context__", None)`; комментарий `:59-60` утверждает «the shape `ClearingService._postgres_error_codes` already uses», но клиринг с 2026-09-12 ходит только по `orig`/`__cause__` (`clearing/service.py:385-388`), а `payments/service.py:71-83` объясняет, почему `__context__` опасен.
Почему это важно: `23505`, унаследованный через `__context__` от ранее обработанной ошибки, может переименовать посторонний `IntegrityError` в «Active trustline already exists» (409). Риск узкий (сначала сверяется имя констрейнта), но комментарий вводит в заблуждение.
Минимальное исправление: `orig`/`__cause__`, как у соседей; исправить комментарий. Это ровно target F-016-3 (`iter_exception_chain` в `app/utils/db_errors.py`).
Covered-by: 016 (F-016-3) — `__context__`-расхождение в спеке не названо; добавить в её репродьюсер. Contract: no.

### N-8 — нетто-позиция ~9 способами
Evidence: денежное ядро — `invariants.py:237-257` (`_calculate_net_position`, два запроса на участника; клиринг зовёт его в цикле, `clearing/service.py:2034-2038`) и `money_boundary.py:334-373` (`_snapshot_net_positions`, два сгруппированных запроса на множество). Третья — `invariants.py:62-85` (`_compute_imbalance` + `check_zero_sum`) живёт только ради тестов после T1402 (гард `tests/integration/test_p014_t1402_zero_sum_is_not_published_as_a_check.py:140`). Представления: `admin.py:812-835` (SQL `union_all`), `admin.py:1633-1655` / `:2006-2028`, `admin/metrics.py:366-391` (per-side `ROUND_DOWN`, F-015-11), `snapshot_builder.py:201-280`, `viz_patch_helper.py:89-102`, `:265-290`, `balance/service.py:146-158`.
Почему это важно: на денежном пути два владельца одного факта «credits − debts» (проверка платежа и нейтральность клиринга); расхождения сейчас нет. Представления расходятся (N-1).
Минимальное исправление: клиринг — `self._boundary._snapshot_net_positions(...)` вместо цикла (при переписывании исполнения в 023); `_compute_imbalance`/`check_zero_sum` — отдельный cleanup «живёт только ради тестов». Представления — не трогать сверх N-1.
Covered-by: 023 частично. Contract: no.

### N-9 — `used/available` линии: семь копий
Evidence: владелец `trustlines/service.py:754-761` — для закрытой линии `used = 0` («Reporting the successor's debt as a closed line's `used` would show an operator a foreign amount»). SQL-копии `admin.py:657-658`, `:778-779`, `:1768` (graph), `:2129` (ego) — `func.coalesce(Debt.amount, 0)` по паре без учёта статуса; `admin/metrics.py:500`; `snapshot_builder.py:236-238` клампит `available` в `0`, остальные нет.
Почему это важно: граф админки через `_dedupe_trustline_rows` показывает закрытую инкарнацию, если живой нет, и берёт для неё долг пары — ровно то, от чего защищается сервис. Закрытие требует нулевого долга, поэтому расхождение латентно (INFERENCE).
Минимальное исправление: одна приватная SQL-функция `_used_expr()` в `admin.py` с `case((TrustLine.status == 'closed', 0), else_=coalesce(Debt.amount, 0))`.
Covered-by: none. Contract: no.

### N-10 — метрики платежа, 18 копий
Evidence: `payments/service.py:196-201`
```python
try:
    from app.utils.metrics import PAYMENT_EVENTS_TOTAL
    PAYMENT_EVENTS_TOTAL.labels(event="create", result="start").inc()
except Exception:
    pass
```
ещё 17 раз в том же файле; в клиринге отказ счётчика логируется (`clearing/service.py:1114-1117`). Уже есть `_count_create_start` (`payments/service.py:193`), но им пользуется один вызов.
Минимальное исправление: локальный `_count(event, result)` в `payments/service.py`; «глотать или логировать» — одно решение на файл. Covered-by: none. Contract: no.

### N-11 — мёртвые и расходящиеся дефолты настроек
Evidence (`settings_defaults.py`): `payments/service.py:2259` и `:2784` — `getattr(settings, "COMMIT_RETRY_ATTEMPTS", 1)` при `config.py:190` `= 3`; `router.py:443` — `…ROUTING_PATH_FINDING_TIMEOUT_MS", 50` при `config.py:181` `= 500`; `deps.py:212` — `ADMIN_DEV_ALLOWLIST ""` при `'127.0.0.1,::1'`; `balance/service.py:54` читает `BALANCE_SUMMARY_CACHE_MAX_ENTRIES`, которого в `Settings` нет (всегда 1024, через env не настраивается). Хвостовое `or X` дополнительно превращает законный `0` в `X`.
Почему это важно: читатель денежного пути видит «1 попытка», работают 3. Минимальное исправление: `settings.NAME` без `getattr`; добавить поле или убрать чтение. Covered-by: none. Contract: no.

### N-12 — обёртка проверки подписи ×7
Evidence: `trustlines/service.py:154-158`, `:347-351`, `:472-476`, `participants/service.py:53-57`, `:171-175`, `payments/service.py:1256-1271`, `auth/service.py:79-84`; правило «`canonical_json` вне `try`, иначе отказ float перемаркируется в 401» внесено в каждую копию отдельно (комментарии `trustlines/service.py:145-153`, `:345-346`, `:469-471`). «Missing signature» повторена трижды в трастлайнах.
Минимальное исправление: `verify_signed_payload(public_key, payload, signature)` рядом с `verify_signature` в `app/core/auth/crypto.py` (канон + отсутствие + перемаркировка); метрика платежа остаётся у вызывающего. Covered-by: none. Contract: no.

### N-13 — «наивная метка из БД → UTC» после 017
Evidence: все `mapped_column(DateTime…)` в `app/db/models` — `timezone=True` (grep иных форм не нашёл); тем не менее `schemas/admin.py:108-113`, `:160-165`, `schemas/equivalents.py:26-31`, `schemas/integrity.py:9-12`, `schemas/trustline.py:25-29` и 7 веток в `admin/metrics.py` (напр. `:582`, `:603-604`) приписывают UTC «naive database timestamp». Пять копий политики, оставшейся от SQLite.
Минимальное исправление: verify first — подтвердить, что схемы не строятся из не-БД источников (fixtures симулятора); затем удалить. Covered-by: none (кандидат в cleanup 017). Contract: no.

### N-14 — статус участника UI↔DB
Evidence: `admin.py:149-153` `banned → ["deleted", "left"]`; `real_scenario_seeder.py:115-118` `banned → "deleted"`, `frozen → "suspended"`. Минимальное исправление: одна таблица соответствия в `app/core/participants/`. Covered-by: 021 (сидер) частично. Contract: no.

### N-15 — `DeclaredFlow` ≡ `PaymentFlow`
Evidence: `book.py:248-255` и `payments/service.py:628-644` — те же четыре поля; `payments/service.py:1956-1962` переупаковывает одно в другое поле за полем. Разные слои — допустимо; документировать, не патчить, либо `DeclaredFlow = PaymentFlow` + свободная `flow_as_intent(flow)`. Covered-by: none.

### N-16 — сумма нетто через эквиваленты
Evidence: `admin.py:813-835` — `debt_base` фильтруется по `eq_code` только если он задан; при `equivalent=None` `func.sum(delta)` группируется по участнику, а не по (участнику, эквиваленту). INFERENCE: складываются UAH и HOUR. Covered-by: none. Contract: возможно (описание ответа в OpenAPI не сверено).

## Что не проверено

- `app/api/v1/simulator.py` (3053) — прочитаны только кандидаты генератора; блок `action_*` (`:972-2322`) целиком в 021, его внутренние клоны (4× преамбула периметра `:1020-1045`, `:1273-1293`, `:1450-1470`, `:1626-1646`) не заводил.
- `app/core/ledger/book.py`, `reconciliation.py`, `app/db/journal_*` — генератор дал только DDL/импорты; глазами не читал.
- Логирование с одинаковыми полями и уникальность имён событий — не проверял.
- Пагинация: ручной кламп в `integrity.py:278-280` против `Query(ge=1)` в админке — отмечено, не заведено.
- Router против `_segment_capacity` по политикам линии (`can_be_intermediate`, `blocked_participants`) — не сверял, только формулу.
- `real_tick_metrics.py:60-66` считает `total_debt` по всему эквиваленту, а не по периметру рана — замечено, не проверено, как это соотносится с изоляцией ранов (скоуп 021).
- UI (F-016-6/7/8) — проверены только якоря для таблицы статуса.
- Ни один репродьюсер не запускался (read-only, без БД): N-1, N-3, N-5, N-6 — выводы по коду.

## Оценка направления текущего плана

- **016** устарела якорями (engine, recovery удалены) и недосчитывает F-016-3: извлекателей пять, два в симуляторе (скоуп 021), трастлайны нарушают правило про `__context__`. F-016-5 можно закрыть записью «исчезла с 019», остаток `payments/service.py:881-897` передать в cleanup. Для 016 стоит добавить N-1 как девятую находку — единственный найденный случай, где копия уже нарушает зафиксированный инвариант.
- **022** удаляет симуляторные viz-модули, но admin-копии viz-правил и атомов (`admin.py` snapshot/ego) остаются вне её owner surface. Их надо либо назвать в 022, либо записать как сознательно оставленные.
- **021** не называет `precision or 2` (N-3) в `inject_executor`/`real_clearing_engine` и два предиката SQLSTATE симулятора (`money_replay.py:133-138`, `real_runner_impl.py:69-76`). Если `tick.py` их заменит, достаточно строки в Changelog, что предикаты берутся из домена, а не копируются заново.
- **023** оставляет `verify_clearing_neutrality` на каждом цикле; при переписывании исполнения естественно перейти на batched `_snapshot_net_positions` (N-8).
- N-4 и N-5 — не для 016 (общий примитив там запрещён) и не для 023: это решения («политика повторной отмены», «есть ли ребро без встречной линии»), которые надо записать до свода копий.
