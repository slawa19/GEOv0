# 007 — Simulator analytics surface

- **Date:** 2026-08-11 (авторизована и разблокирована 2026-08-20)
- **Status:** **AUTHORIZED** — владелец 2026-08-20 авторизовал программу расширенным набором.
  Предусловие F-007-1 остаётся **первым исполняемым блоком** (T708–T712): пока честность данных не
  восстановлена, фича не строится — цена ошибки не «пустой экран», а правдоподобный график из
  SHA-256. Порядок: сначала снятие F-007-1, затем поглощённые находки, затем сама панель.
- **Расширенный набор (решение владельца 2026-08-20).** 007 объявлена **единственным владельцем
  честности simulator analytics**: в неё поглощаются находки, легшие на ту же поверхность в волне 6
  и T808, чтобы одна причина не резалась на несколько slice. Поглощены:
  - `B-D1-002` (P2) — денежные серии `total_debt` и `clearing_volume` идут `float` от истока
    (`app/core/simulator/real_tick_metrics.py:70` — сумма долгов из БД) через
    `real_clearing_engine.py:372,596`, колонку `Float` (`app/db/models/simulator_storage.py:73`) и
    `MetricPoint.v: float` до `type: number` в каноне. Намерение владельца зафиксировано документом
    `docs/ru/simulator/backend/simulator-domain-model.md:159-164`: обе серии — «сумма в выбранном
    эквиваленте», то есть деньги, и §8 применяется прямо.
  - `T808-001` (P3) — запись внутри GET `bottlenecks` (`metrics_bottlenecks.py:344-369`,
    вызывающий `app/api/v1/simulator.py:2604`): `add_all` + `commit` в обработчике GET, append-only,
    под `except Exception: pass` без лога. Очистка существует
    (`scripts/cleanup_simulator_runs.py:91`, по неактивным прогонам), **лимита числа строк для
    активных прогонов нет** — §12 требует и TTL, и лимит.
  - `C-D1-004` (P3) — семь ключей метрик объявлено, пять производится.
- **Решение владельца по форме правки `B-D1-002` (2026-08-20).** Переводить серию на
  `Numeric`/decimal strings. Старые `float`-строки **архивировать, а не выдавать за восстановленные
  точные деньги**: точность уже записанной истории необратимо утрачена, и §1 требует, чтобы
  отсутствующее измерение отличалось от нулевого. Миграция ведётся **отдельным slice** и не
  смешивается со снятием F-007-1 — необратимый шаг не бандлится с обратимыми.
- **Owner surface:** `simulator-ui/v2/src/` (api, composables, components, ui-kit) **и**
  `app/core/simulator/metrics_bottlenecks.py`, `real_tick_metrics.py`, `storage.py`,
  `app/db/models/simulator_storage.py`, `app/schemas/simulator.py`, `api/openapi.yaml` (маркер
  деградации требует изменения канона: обе схемы несут `extra="forbid"`, внеконтрактному флагу места
  нет).
- **Внешнее ревью обязательно** перед закрытием: пачка меняет защищённые контракты §8 (канон,
  денежная семантика, миграция) — триггер `AGENTS.md` §15 срабатывает по трём пунктам сразу.
- **Source spec:** `docs/ru/simulator/ui-analytics-dashboard-spec.md` остаётся живой продуктовой спецификацией.
- **Единственная фича, а не долг.** По файлам по-прежнему не пересекается с 002–006, поэтому идёт
  параллельно любой волне — блокировка была внутренней, а не межпрограммной.

## Блокирующее предусловие: честность данных (P2)

### F-007-1 — `GET /metrics` и `GET /bottlenecks` в реальном режиме молча отдают синтетику

Внешнее ревью утверждало, что бэкенд аналитики выдумывает данные. Независимая проверка **подтвердила
это и показала, что положение хуже заявленного**. Пока это не исправлено, программа 007 не должна
стартовать: цена ошибки не «пустой экран», а «правдоподобный график из SHA-256».

**Синтетика достижима именно в `real`, а не только на фикстурах.** Оба fallback-а стоят *внутри*
ветки реального режима: `metrics_bottlenecks.py:89` и `:217` — `if self._db_enabled() and run.mode
== "real":`, `try` открывается внутри этой ветки, а `except` проваливается **вниз, в синтетический
генератор**. То есть отказ БД в реальном прогоне не даёт ошибку — он даёт выдуманный ответ.

**Обработчики отказа:**

- метрики, `:137-144`: `except Exception:` → комментарий `# Fall back to synthetic below.` →
  `self._logger.debug("simulator.metrics.db_query_failed_falling_back ...", exc_info=True)`.
  Лог **есть**, но на уровне DEBUG, который в проде обычно выключен, — практически беззвучно;
- bottlenecks, `:280-282`: `except Exception:` → `# Fall back to synthetic below.` → `pass`.
  **Логирования нет вообще.**

**Fallback не возвращает пусто — он синтезирует.** `build_metrics` `:146-203` строит
псевдослучайные ряды через SHA-256-хелпер `seed_f()` для всех пяти ключей (авторский комментарий
`:164`: «Deterministic pseudo-dynamics; good enough for UI scaffolding»). `build_bottlenecks`
`:284-345` выдумывает `used_ratio` из `seed_f(...)` (`:314-315`, комментарий `# Synthetic used
fraction`) и выводит из него `score`, `reason_code`, `label`, `suggested_action`. Ответ
схемно-идентичен настоящему — **вызывающая сторона не может их различить**.

**Худшая находка — unbound-local, из-за которого bottlenecks синтезируются на штатном пути, а не
только при отказе БД.** `:228` `if latest is not None:` … `:234` `rows = (await
session.execute(q)).scalars().all()` — `rows` связывается **только внутри этой ветки**, — затем
`:237` `for r in rows:` уже снаружи. Для реального прогона, у которого bottlenecks просто ещё не
персистились (`latest is None` — нормальное состояние в начале прогона), `:237` бросает
`UnboundLocalError`; это `Exception`; его глотает беззвучный `except Exception: pass` (`:280-282`);
наружу уходят выдуманные bottlenecks, выведенные из trustlines сценария. Итог: **real-mode
`GET /bottlenecks` отдаёт фальсификат на обычном условии «таблица пуста», молча.**

**Авторский замысел подтверждает, что это дефект, а не решение.** Комментарий `:347-349` — «Keep
writing synthetic bottlenecks only for non-real mode (UI scaffolding); real-mode is DB-first and
writes from the runner» — охраняет `if self._db_enabled() and run.mode != "real":` (`:349`).
**Писатель отгорожен от реального режима; читатель — нет.**

**Маркера синтетики в ответе не существует.** `MetricsResponse`
(`app/schemas/simulator.py:465-476`) и `BottlenecksResponse` (`:518-525`) несут только
`api_version`, `run_id`, `equivalent`, оконные поля и `series`/`items`, и обе заканчиваются
`model_config = ConfigDict(extra="forbid")`. Значит дело не только в отсутствии флага — **места под
внеконтрактный флаг тоже нет**: любой признак «данные деградированы» требует изменения схемы и
OpenAPI.

**Отдельный второй режим отказа — carry-forward.** `:112-126`: если запрос к БД **успешен**, но
вернул **ноль строк**, исключения нет, значит и синтетического fallback-а нет; вместо этого
`last_val = 0.0`, и ресемплер выдаёт ряд из сплошных нулей, который на экране выглядит как честная
нулевая метрика. Авторский комментарий `:112-113`: «Resample persisted tick metrics to
(from_ms..to_ms, step_ms) using carry-forward. This guarantees MetricPoint.v is always numeric» —
гарантия «всегда число» и есть источник проблемы. **«Нет данных» неотличимо от «измерено ноль»**, и
точно так же «нет данных до момента t» неотличимо от «до t было ноль».

**Тестов нет.** Ни один тест не ссылается на `build_metrics`, `build_bottlenecks` или
`metrics_bottlenecks`. `tests/unit/test_simulator_write_tick_metrics_upsert.py` покрывает только
**писателя**.

### Критерии снятия блокировки

- [ ] отказ БД в реальном режиме **никогда** не приводит к выдаче синтетических данных;
- [ ] отсутствие измерения отличимо от измеренного нуля — и в API-ответе, и на экране;
- [ ] синтетические данные появляются **только** в явно синтетических режимах (`run.mode != "real"`);
- [ ] оба пути — DB-backed и fallback — покрыты тестами.

## Problem

Бэкенд и API-клиент аналитики готовы много месяцев, потребителей нет:
`GET /simulator/runs/{run_id}/metrics` и `/bottlenecks` реализованы
(`app/api/v1/simulator.py:2691`, `:2710`), `getMetrics`/`getBottlenecks` реализованы
(`simulator-ui/v2/src/api/simulatorApi.ts:154`, `:169`) — и имеют **ноль call-site'ов** во всём
`simulator-ui/v2`.

## Скрытая стоимость: типы фронтенда противоречат схеме бэкенда

Продуктовая спека этого не упоминает и исходит из того, что клиент готов. Он не готов.
**Важно про степень остроты:** сегодня ничего не сломано — у `getMetrics`/`getBottlenecks`
ноль call-site'ов, то есть это латентный мёртвый код с неверным контрактом, а не активный баг.
Но первая же панель, написанная по текущим типам, прочитает `undefined`, поэтому переписывание
типов идёт первой задачей и предшествует любому UI-коду.

| Тип | Сейчас на фронте | Бэкенд отдаёт |
|---|---|---|
| `MetricsResponse` | `{api_version?, equivalent, points: Array<{t_ms} & Record<string, number\|null>>}` (`simulatorTypes.ts:328-332`) | `{api_version, run_id, equivalent, from_ms, to_ms, step_ms, series: MetricSeries[]}`, где `MetricSeries = {key, unit, points}` (`app/schemas/simulator.py:465-476`, `:457-462`). **Поля `series` во фронтовом типе нет вообще** |
| `BottleneckItem` | `{kind, score, from?, to?, details?}` (`simulatorTypes.ts:334-340`) | `{target: BottleneckTargetEdge\|Node, score, reason_code, label?, suggested_action?}` (`app/schemas/simulator.py:507-515`). **Нет ровно тех четырёх полей**, на которых держатся §4.1 и критерий приёмки №2 продуктовой спеки |
| `BottlenecksResponse` | `{api_version?, equivalent, items}` (`simulatorTypes.ts:342-346`) | плюс `run_id: str` (`app/schemas/simulator.py:521`) — во фронтовом типе поля нет |
| `api_version` (оба ответа) | необязательный (`api_version?`) | всегда присутствует: `Field(default=SIMULATOR_API_VERSION)` (`app/schemas/simulator.py:466`, `:519`) — значение по умолчанию есть, поэтому поле никогда не опускается. Фронтовый `?` неверен |
| `MetricSeriesKey` | **ЗАКРЫТО 2026-08-20 (T713).** Было: спека §2.1 перечисляет 5 значений, канон 5, pydantic 7, читатель 5, писатель 7. Стало: все четыре стороны на **7**, единица новых серий `count`. Прежняя оценка «корректно описывает то, что API отдаёт сегодня** | схема объявляет 7 (`app/schemas/simulator.py:443-451`), но эмиттер жёстко зашивает первые пять (`app/core/simulator/metrics_bottlenecks.py:80-86`, тот же список фильтрует SQL-запрос `:100`). TS-union обязан быть на 7 значений — он описывает объявленную response-модель, а не текущую выдачу |

**Настоящий дефект здесь — на бэкенде, а не в продуктовой спеке.** `active_participants` и
`active_trustlines` действительно считаются (`app/core/simulator/real_tick_metrics.py:104-123`)
и действительно персистятся (`app/core/simulator/storage.py:353-362`), но не попадают в ответ
`GET /metrics`: две серии persist-only. См. T707. — **ЗАКРЫТО 2026-08-20 срезом T713:** обе серии теперь отдаются `GET /metrics`, четыре стороны (канон, pydantic, читатель, писатель) согласованы машинной проверкой, единица — `count`. Абзац сохранён как запись того, чем дефект был.

Плюс отсутствие runtime-валидации: `getSnapshot` идёт через
`simulatorContractJson(..., decodeGraphSnapshotResponse)` (`simulatorApi.ts:145-152`), а
`getMetrics`/`getBottlenecks` — через голый `httpJson` (`:166`, `:180`). Значит рассогласование
выше не упадёт явно, а тихо даст пустой экран.

## Что уже есть и переиспользуется

- Подсветка ребра: `addActiveEdge(key, ttlMs)` — `useOverlayState.ts:106`, экспорт `:447`,
  проводка `useAppFxOverlays.ts:108`, `useSimulatorApp.ts:1041,1061`.
- Состояние прогона: `runStatus` — `useSimulatorRealMode.ts:47,405,886,965`.

## Чего нет вообще

`RealMetricsPanel.vue`, `MetricsKpiCard.vue`, `BottlenecksList.vue`, `useMetricsPolling.ts`,
раскладка под боковую панель, переключатель в нижнем HUD, и — net-new API — фокус камеры
на ребре: `useCamera.ts:347-362` возвращает `camera`, `panState`, `wheelState`, `resetCamera`,
`getWorldBounds`, `clampCameraPan`, `worldToScreen`, `screenToWorld`, `worldToCssTranslate`,
`clientToScreen`, `onPointerDown`, `onPointerMove`, `onPointerUp`, `onWheel`.
`focusOnEdge`/`focusOnNode` отсутствуют. (`getCameraClampInfo` — внутренний хелпер `:89`,
используется на `:162,216,265`, наружу **не** экспортируется.)

## Две поправки к продуктовой спеке

1. **§5 задаёт сырой CSS с хардкодом `#1e293b` / `#ef4444`.** Это нарушает
   `simulator-ui/v2/src/ui-kit/AI-AGENT-GUIDE.md`. Реализовывать через токены `--ds-*` и
   зарегистрировать поверхность в `src/ui-kit/overlaySurfaceCatalog.ts`.
2. **§7.1 показывает `from_ms`/`to_ms`/`step_ms` как необязательные.** Сервер требует все три:
   `from_ms` `:2695`, `to_ms` `:2696`, `step_ms` `:2697` (`app/api/v1/simulator.py:2695-2697`;
   `:2694` — это `equivalent`). Опрос обязан их вычислять.

Мелкий дрейф имён: спека называет компоненты `RealHudTop`/`RealHudBottom`, в дереве это
`TopBar.vue` / `BottomBar.vue`.

## Non-goals

- Изменение схемы бэкенда или добавление новых эндпоинтов.
- История метрик за пределами текущего прогона.
- Экраны Bottlenecks/Concentration в Admin UI — другая поверхность (см. [`../BACKLOG.md`](../BACKLOG.md)).

## Verification plan

1. Контрактный тест: декодер метрик и bottlenecks принимает реальный ответ бэкенда и отвергает
   старую форму. Должен падать на текущих типах.
2. Unit: `useMetricsPolling` опрашивает только при `runStatus.state === 'running'` и корректно
   останавливается.
3. Component: `BottlenecksList` рендерит `reason_code`, `label`, `suggested_action` и вызывает
   фокус на ребре.
4. Playwright: переключатель панели, сохранение предпочтения, отсутствие регрессии раскладки.
5. Визуальная проверка: ни одного хардкод-цвета вне токенов `--ds-*`.

## Tasks

| ID | Задача | Статус |
|---|---|---|
| T700 | Переписать `MetricsResponse`, `BottleneckItem`, `BottlenecksResponse` в `simulatorTypes.ts:328-346` под `app/schemas/simulator.py:443-525`; `MetricSeriesKey` на 7 значений, добавить `run_id` в `BottlenecksResponse`, сделать `api_version` обязательным. Готовый текст — в приложении ниже. **История git подтверждает, что это не «фронт против старого бэкенда»:** `git log -S 'MetricsResponse' -- simulator-ui/v2/src/api/simulatorTypes.ts` даёт ровно один коммит — `c2220cf` (2026-01-29), он же коммит создания файла; форма на бэкенде — тоже ровно один коммит, `5f26ea0` (2026-01-28). Схема бэкенда приземлилась **на день раньше** фронтовых типов: типы были спекулятивными с самого начала и ни разу не сверялись | `[x]` — закрыт 2026-08-21 |
| T701 | Декодеры + перевод `getMetrics`/`getBottlenecks` на `simulatorContractJson`. Опорный факт: коммит `8494992 fix(simulator): validate critical REST responses` ввёл `simulatorContractJson` (`simulatorApi.ts:39-47`) и провёл через него сценарные и lifecycle-вызовы — `listScenarios` `:50`, `getScenario` `:54`, `getSnapshot` `:147`, `setIntensity` `:134` и др. `getMetrics` `:166` и `getBottlenecks` `:180` в эту волну валидации не попали | `[x]` — закрыт 2026-08-21 |
| T702 | `useMetricsPolling.ts` (5 c, гейт по `runStatus`, вычисление `from_ms`/`to_ms`/`step_ms`) | `[x]` — закрыт 2026-08-21 |
| T703 | `MetricsKpiCard.vue`, `BottlenecksList.vue`, `RealMetricsPanel.vue` на `ds-*`; регистрация в `overlaySurfaceCatalog.ts` | `[x]` — закрыт 2026-08-21 |
| T704 | `focusOnEdge` в `useCamera.ts` + проводка через `useAppViewWiring.ts` | `[x]` — закрыт 2026-08-21 |
| T705 | **Панель как зарегистрированная overlay-поверхность** (не grid): новое семейство в `overlaySurfaceCatalog.ts`, переключатель в `BottomBar.vue`, сохранение в `usePersistedSimulatorPrefs.ts`. Подробности и история задачи — раздел «T705: сохранена и перепрописана» ниже | `[x]` — закрыт 2026-08-21 |
| T706 | Обновить `docs/ru/simulator/ui-analytics-dashboard-spec.md`: убрать хардкод-CSS из §5, поправить §7.1 и имена компонентов | `[ ]` |
| T707 | Экспонировать `active_participants` / `active_trustlines` в `GET /metrics` (`metrics_bottlenecks.py:80-86`) либо явно задокументировать их как persist-only | `[x]` — закрыт срезом T713 2026-08-20 |
| T708 | **Unblock F-007-1.** Починить unbound-local: `rows` связывается только внутри `if latest is not None:` (`metrics_bottlenecks.py:228`, присваивание `:234`), а читается снаружи (`:237`). Инициализировать `rows` пустым списком до ветки — тогда «bottlenecks ещё не персистились» становится честным пустым ответом, а не `UnboundLocalError` | `[x]` |
| T709 | **Unblock F-007-1.** Убрать оба беззвучных провала в синтетику (`:137-144` метрики, `:280-282` bottlenecks): либо явная ошибка (5xx/явный код), либо явное поле `degraded`/`source` в ответе. **Второй вариант требует изменения схемы и OpenAPI** — обе response-модели закрыты `ConfigDict(extra="forbid")` (`app/schemas/simulator.py:476`, `:525`), внеконтрактный флаг физически некуда положить. Выбор варианта — решение владельца поверхности, но «молча синтетика» из списка выбывает | `[x]` |
| T710 | **Unblock F-007-1.** Отказ запроса к БД логировать на **WARNING**, а не DEBUG (`:139` `self._logger.debug(...)`), и добавить симметричный лог в ветку bottlenecks, где сейчас голый `pass` (`:282`) | `[x]` |
| T711 | **Unblock F-007-1.** Сделать «нет измерения» отличимым от измеренного нуля в carry-forward-ресемплере (`:112-126`): `last_val = 0.0` до первой точки ряда — это подделка. Либо не эмитить точки до первого измерения, либо провести `null` через `MetricPoint.v` (тоже правка схемы) — и отразить решение в декодерах T701 и в отрисовке T703 | `[x]` |
| T712 | **Unblock F-007-1.** Тесты на оба пути `build_metrics` / `build_bottlenecks`: DB-backed (есть строки), пустая таблица в реальном режиме (не должно быть ни синтетики, ни `UnboundLocalError`), отказ БД в реальном режиме (не должно быть синтетики), синтетический режим (`run.mode != "real"` — синтетика допустима). Сегодня на `metrics_bottlenecks` не ссылается ни один тест; `tests/unit/test_simulator_write_tick_metrics_upsert.py` покрывает только писателя | `[x]` |
| T713 | **Поглощено из волны 6 (`C-D1-004`).** Семь ключей метрик объявлено, пять производится: привести объявление и производство к одному множеству либо явно пометить непроизводимые как недоступные — «нет измерения» отличимо от нуля по тому же правилу, что T711 | `[ ]` |
| T714 | **Поглощено из T808 (`T808-001`, P3).** Запись внутри GET `bottlenecks` (`metrics_bottlenecks.py:344-369`): вынести из обработчика запроса либо ограничить числом строк. Очистка по неактивным прогонам есть (`scripts/cleanup_simulator_runs.py:91`), лимита для активных нет — §12 требует обоих. Отказ обязан логироваться, а не глохнуть в `except Exception: pass` | `[ ]` |
| T715 | **Поглощено из волны 6 (`B-D1-002`, P2). Отдельный slice, необратимый.** Денежные серии `total_debt` и `clearing_volume` перевести на `Numeric`/decimal strings по всему пути: `real_tick_metrics.py:70`, `real_clearing_engine.py:372,596`, колонка `Float` (`simulator_storage.py:73`), `MetricPoint.v`, канон. Старые `float`-строки **архивировать**, не выдавая за восстановленные точные деньги (решение владельца 2026-08-20). Гейты: миграция + Alembic single head + Postgres | `[x]` — закрыт 2026-08-20 после трёх P1 внутреннего ревью |
| T716 | **Долг, порождённый срезом снятия F-007-1 (2026-08-20), назван честно, а не спрятан.** Два хвоста: (а) роуты `app/api/v1/simulator.py:2585,2604` не объявляют `responses={503: ...}`, поэтому канон объявляет 503, а генерируемая FastAPI схема — нет; это **сознательно заведённое canonical-only расхождение**, залоченное ратчетом `ERROR_RESPONSE_DRIFT_SHA256`. Канон — авторитет №1 по §8, поэтому важная сторона закрыта, но клиент, генерируемый из приложения, 503 не увидит. (б) аннотации соседей `real_tick_payments_coordinator.py:22,109` и `real_tick_persistence.py:81` объявляют `dict[str, dict[str, float]]`, тогда как значения теперь `Optional[float]`; mypy в гейтах нет, поведение не затронуто, ошибка молчит (в) параметр `utc_now` в `MetricsBottlenecks.__init__` стал мёртвым после T714; единственный сайт конструирования — `runtime_impl.py:133-140`, вне выданной срезу поверхности, поэтому оставлен с датированным комментарием | `[ ]` |
| T717 | **Остатки среза T715, названные и не спрятанные.** (а) `app/core/simulator/storage.py:621` — `write_tick_bottlenecks` несёт тот же откат чужой сессии с проглатыванием, что был исправлен SAVEPOINT'ом в `write_tick_metrics`. Это **чистый `B-A2b-001`** реестра 008 (кандидат в P1, ждёт триажа T810); срез его достижимость не менял, поэтому чинить здесь значило бы забрать решение у владельца. (б) Потолок `NUMERIC(20,8)` — 12 целых цифр — больше не разрушителен (savepoint) и задокументирован, но прогон с `total_debt ≥ 10^12` не получит серию вовсе, отказ виден только в логе на ERROR, а **наружу в `GET /metrics` сигнала нет**: carry-forward нарисует последнее удачное измерение живой линией. Остаточный риск того же семейства, что F-007-1. (в) `simulator-ui/v2/src/api/simulatorTypes.ts:327-331` объявляет форму, которой бэкенд не отдавал и до среза; `v` теперь строка — учесть в T700/T701 | `[ ]` |

## T705: сохранена и перепрописана

**Внешнее ревью потребовало удалить T705** как описывающую несуществующие сущности. Проверка
подтвердила фактическую часть претензии и отвергла вывод: задача **сохранена**, но её *подход*
переписан.

Ничего из описанного в прежней формулировке в коде действительно нет:

- `.with-panel` — **ноль вхождений** во всём `simulator-ui/v2/src`;
- `SimulatorAppRoot.vue:1114` рендерит `class="root ds-ov-vars"`, а его scoped-стиль (`:1413` и
  далее) определяет только `.wm-layer` и `.sar-interact-history-overlay`;
- `.root` определён в `simulator-ui/v2/src/App.css:1-36` как full-bleed хост-контейнер
  (`position: relative; width/height: 100%; overflow: hidden`) плюс шкала z-index; grid-раскладки
  в нём нет;
- `BottomBar.vue` содержит только `toggleDemoUi` (`:35`, `:271`) — переключателя панели нет;
- `usePersistedSimulatorPrefs.ts:23-30` объявляет ровно 7 ключей `PersistedKey`, ни одного про панель.

**Но удалять задачу нельзя — нужно переписать её подход.** `overlaySurfaceCatalog.ts:9-19`
перечисляет `OverlaySurfaceFamily`: `interact-panel | inspector-card | hud-bar | hud-dropdown |
notification-toast | bottom-overlay | dev-overlay | tooltip | canvas-overlay | message-overlay` —
семейства «пристыкованная боковая панель» среди них нет, а канва живёт как `position: absolute;
inset: 0` (`App.css:38-42`). Превращение `.root` в CSS-grid сдвинуло бы канву и сломало всю
overlay-модель. Поэтому T705 обязана выражать панель как **зарегистрированную в каталоге
overlay-поверхность** (новое семейство + токены отступов), а не как grid на корне.

## Приложение: целевые типы (готово к вставке, заменяет simulatorTypes.ts:328-346)

```ts
export type MetricSeriesKey =
  | 'success_rate'
  | 'avg_route_length'
  | 'total_debt'
  | 'clearing_volume'
  | 'bottlenecks_score'
  | 'active_participants'
  | 'active_trustlines'

export type MetricUnit = '%' | 'count' | 'amount' | null

// 2026-08-20 / T715: `v` — decimal string и nullable. `null` = измерения не было,
// строка = было; `"0.00000000"` — измеренный ноль. Числом `v` не был с момента
// перевода денежных серий на Numeric: вставка прежней редакции вернула бы
// и float над деньгами, и неотличимость "нет данных" от нуля.
export type MetricPoint = { t_ms: number; v: string | null }

export type MetricSeries = { key: MetricSeriesKey; unit: MetricUnit; points: MetricPoint[] }

export type MetricsResponse = {
  api_version: string
  run_id: string
  equivalent: string
  from_ms: number
  to_ms: number
  step_ms: number
  series: MetricSeries[]
}

export type BottleneckReasonCode =
  | 'LOW_AVAILABLE'
  | 'HIGH_USED'
  | 'FREQUENT_ABORTS'
  | 'TOO_MANY_TIMEOUTS'
  | 'ROUTING_TOO_DEEP'
  | 'CLEARING_PRESSURE'

export type BottleneckTargetEdge = { kind: 'edge'; from: string; to: string }
export type BottleneckTargetNode = { kind: 'node'; id: string }
export type BottleneckTarget = BottleneckTargetEdge | BottleneckTargetNode

export type BottleneckItem = {
  target: BottleneckTarget
  score: number
  reason_code: BottleneckReasonCode
  label?: string | null
  suggested_action?: string | null
}

export type BottlenecksResponse = {
  api_version: string
  run_id: string
  equivalent: string
  items: BottleneckItem[]
}
```

- `from` (не `from_`) корректно на проводе — `app/schemas/simulator.py:491` использует
  `Field(alias="from")`, FastAPI сериализует `by_alias=True`.
- `label`/`suggested_action` объявлены `Optional[...] = None` без `exclude_none`, то есть всегда
  присутствуют, но могут быть `null` — отсюда `?: string | null`.
- `unit` присутствует всегда, но может быть `null` — обязательный ключ с `| null`, не `unit?`.
- union `MetricSeriesKey` содержит 7 членов по схеме, но потребитель обязан выдержать приход
  только 5 серий — не индексируйте вслепую.
