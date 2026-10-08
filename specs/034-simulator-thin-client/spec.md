# 034 — Симулятор как тонкий клиент домена, стадия 2

- **Date:** 2026-10-08, против `main` на `b37f5ef8`.
- **Status:** **ЧЕРНОВИК 2026-10-08 — дополняется соседним агентом; реализация не авторизована.** Авторизация — консультацией `T3400` (Codex `gpt-6-astra`, high, read-only клон, маркеры вердикта) по протоколу решений 2026-09-21 (`specs/README.md`, «Решение владельца 2026-09-21»); продуктовые развилки П1–П3 ниже — владельцу.
- **Status authority:** метка описательная; завершённость устанавливают записанные evidence с SHA, командой и exit code, а не поле статуса.
- **Owner surface:** `app/api/v1/simulator.py`, `app/core/simulator/**`, `app/schemas/simulator.py` (только с `Contract: yes` и правкой `api/openapi.yaml`), `simulator-ui/v2/src/**` и его тесты, `fixtures/simulator/`, `scripts/run_simulator_ui.ps1`, `docs/ru/simulator/**`. **Не трогается:** `app/core/payments/`, `app/core/clearing/`, `app/core/ledger/`, `money_boundary.py`, `trustlines/service.py` (симулятор — их клиент; нужные публичные методы запрашиваются у программы [035](../035-post-audit-continuation/spec.md) как `Contract: yes`), миграции, `simulator-ui/v1/` (удаление — отдельный cleanup-slice по AGENTS §3), SSE wire shapes без отдельного решения (§8).
- **Origin:** слово владельца 2026-10-08 — «Симулятор будем рефакторить отдельно — заведи на него спеку, которую будет дополнять соседний агент». Источник находок — аудит 2026-10-08 пятью read-only агентами (Opus) на `b37f5ef8`; несущие якоря перепроверены оркестратором и помечены ✓, остальные — чтение агента без перепроверки (помечены △) и требуют подтверждения до постановки задачи. Продолжает [021](../021-simulator-as-domain-client/spec.md) (закрыта 2026-09-28: писатели линий через `TrustLineService`, один тик, адаптивный режим удалён) и забирает **симуляторную часть** черновика [022](../022-ui-shell-reduction/spec.md), как 032 забрала админскую (предложение; фиксируется консультацией `T3400`).

## Problem

После 021 симулятор пишет деньги только через ядро, но остаётся самой крупной и самой слабо структурированной поверхностью репозитория: `app/api/v1/simulator.py` — 3078 строк с бизнес-логикой в хендлерах, `app/core/simulator/` — около 13 тыс. строк с двумя копиями оркестровки клиринга и сидинга, `simulator-ui/v2/src` — 36 тыс. строк продукта и 34 тыс. строк тестов при HTTP-клиенте без таймаута и корреляционного id.

Наблюдаемый вред — не денежный (писатель долгов один, нулевая сумма держится на обоих писателях), а в честности экрана и стабильности рана:

- после рестарта рана платёж с совпавшей суммой может быть показан как проведённый, хотя деньги не двигались (F-034-1);
- проглоченная ошибка PostgreSQL в построении визуального патча отравляет денежную транзакцию, и следующие платежи тика падают с `25P02`, уходя в бюджет ошибок рана (F-034-2);
- зависший REST-вызов из UI держит интерфейс в busy без предела, ошибку из UI нельзя найти в логе (F-034-12);
- штатный клиринговый тик пишет семь строк `WARNING` на эквивалент, артефакты не чистятся (F-034-7, F-034-8).

Всё это — класс 2 по §19.5. Авторизация рефакторинга без воспроизведённой потери требует явного продления исключения «упрощение ядра — цель» (решение владельца 2026-09-24, покрывало 018–021) на симулятор — вопрос П1 ниже.

## Findings

Severity: P1 — ложный успех на экране или остановка рана; P2 — сопровождаемость, дублирование, стабильность без немедленного эффекта; P3 — косметика. Дата проверки — 2026-10-08, HEAD `b37f5ef8`.

### Бэкенд симулятора

| № | Severity | Находка | Evidence | Проверка |
|---|---|---|---|---|
| F-034-1 | P1 | Рестарт рана повторяет ключи идемпотентности. `run_lifecycle.py:496-497` обнуляет `sim_time_ms` и `tick_index` при том же `run_id`; материал ключа `run_id\|tick_ms\|sender\|receiver\|eq\|amount\|seq` (`real_runner_impl.py:693-694`) эпохи рестарта не содержит; ключ становится `tx_id` (`payments/service.py:1079`). Тот же seed даёт тот же порядок плана; платёж рестарта с той же суммой получает сохранённый `COMMITTED` от ядра (контракт повтора `tx_id` с совпавшим отпечатком, 015) и публикуется как `tx.updated`, хотя деньги не двигались; метрика `committed` искажается | `app/core/simulator/run_lifecycle.py:496`, `app/core/simulator/real_runner_impl.py:693`, `app/core/payments/service.py:1079` | ✓ путь по коду; частота совпадений — гипотеза, репродьюсер строится в `T3401` |
| F-034-2 | P1 | Визуальные патчи и fallback-снимки строятся на денежной сессии внутри транзакции под `FOR UPDATE` по линиям периметра, под `except Exception`. `real_payments_executor.py:703` (`patch_session = session`), `:733-806` (`VizPatchHelper.create`, `compute_node_patches`, `build_edge_patch_for_pairs`); `tick.py:533-546` — снимок долгов и точности на той же сессии, ошибка → `debug` → fallback. Любая ошибка PostgreSQL (statement timeout, cancel, `40001`) обрывает транзакцию и проглатывается; следующие платежи падают с `25P02`, который `money_conflict_name` не считает транзитным — класс «проглоченный 40001 отравляет транзакцию» из AGENTS §9. Около 4 лишних запросов на платёж удлиняют окно блокировки | `app/core/simulator/real_payments_executor.py:703,733-806`, `app/core/simulator/tick.py:533-546` | ✓ сессия и `except Exception` по коду; триггер ошибки — гипотеза |
| F-034-3 | P2 | Две копии оркестровки клиринга: `tick.py:826-1050` и хендлер `clearing-real` `simulator.py:1792-1981` плюс построение событий `:170-423`. Оба делают `run_clearing_pass` + `on_committed`, перевод UUID→PID, `clearing.done` с патчами, частичную публикацию при отмене. Копии разошлись: Interact не делает рост доверия после клиринга, `plan_id` в другом формате | `app/core/simulator/tick.py:826`, `app/api/v1/simulator.py:1792` | △ |
| F-034-4 | P2 | Сидинг, множество эквивалентов и периметр — в двух копиях. Сидинг с двойной проверкой под lock: `simulator.py:587-713` и `tick.py:436-454`; множество эквивалентов: `tick.py:459-470` и `real_scenario_seeder.py:136-150`; периметр: тик — `run_perimeter.py:6` из `_real_participants` (БД), API (`simulator.py:715-753`) — полный `build_graph_snapshot` из сценария на каждое действие. Два определения одного полномочия | `app/api/v1/simulator.py:587,715`, `app/core/simulator/tick.py:436,459`, `app/core/simulator/run_perimeter.py:6` | △ |
| F-034-5 | P2 | Мнимая параллельность исполнителя: `Semaphore(max_in_flight)` с дефолтом 1 (`runtime_utils.py:35`) и вдобавок `asyncio.Lock` на одной сессии (`real_payments_executor.py:350-351,427,733`); при этом ~150 строк `create_task`/`as_completed`/переупорядочивания `ready`/`_record_if_ready` (≈530–700). Флаг `SIMULATOR_REAL_MAX_IN_FLIGHT` ничего не меняет (§19 — механизм без эффекта) | `app/core/simulator/real_payments_executor.py:350`, `app/core/simulator/runtime_utils.py:35` | △ |
| F-034-6 | P2 | Подсистема инъекций без продуктового пути: `SIMULATOR_REAL_ENABLE_INJECT: int = 0` (`config.py:263`), `run_full_stack.ps1:1375` тоже ставит 0; ни одна фикстура `fixtures/simulator/*/scenario.json` не содержит `"inject"`. Объём ≈1500 строк рядом с деньгами: `inject_executor.py` 1110, `cache_invalidator.py` 103, транзакции инъекций `real_runner_impl.py:200-556` с локами владельца и ретраями `40001`/`55P03`. Триггер §19.1/19.4 — продуктовый вопрос П2 | `app/config.py:263`, `app/core/simulator/inject_executor.py` | △ объём; флаг ✓ |
| F-034-7 | P2 | Логирование (§12): штатный клиринговый тик пишет на `WARNING` `tick_clearing_enter` (1207), `tick_clearing_done` (1281), на каждый эквивалент `clearing_eq_enter`, `clearing_pass_done` (925), `clearing_eq_done` (971), `clearing_patch_start` (1105), `clearing_patch_done` (1168), `pending_clearing_await_enter` (708). API пишет в `logging.getLogger("uvicorn.error")` (`simulator.py:133`), остальной код — в `__name__` | `app/core/simulator/tick.py`, `app/api/v1/simulator.py:133` | △ |
| F-034-8 | P2 | Артефакты без TTL и лимита (§12): `SIMULATOR_ARTIFACTS_TTL_HOURS: int = 0` (`config.py:249`), `cleanup_old_runs` при ≤0 ничего не делает (`artifacts.py:96-97`); чистка вызывается только в конструкторе рантайма (`runtime_impl.py:146-149`), не после записи; лимита числа ранов нет, `events.ndjson` растёт без предела; переполнение очереди глотается без счётчика (`artifacts.py:325-327`) — отсутствующее измерение неотличимо от нулевого (§1) | `app/core/simulator/artifacts.py:96,325`, `app/config.py:249` | △ |
| F-034-9 | P2 | Молчаливый дрейф топологии в памяти: `_mutate_runtime_trustline_topology_best_effort` (`simulator.py:904-1016`) заканчивается `except Exception: return` без лога; `_compute_viz_patches_best_effort` (`:420-421`) — `return None, None`, `:366` — `pass`. Расхождение `_scenario_raw`/`_edges_by_equivalent` с БД никто не видит, планировщик планирует по ним (§9, §12) | `app/api/v1/simulator.py:904,420,366` | △ |
| F-034-10 | P2 | У heartbeat нет границы исключений: `runtime_impl.py:887-983` ловит только `CancelledError`; исключение из `publish_run_status`, `get_run` после вытеснения или `rr.fail_run` внутри except-ветки тика (`tick.py:396-422`) тихо убьёт задачу — ран останется `running` без тиков и без `last_error` | `app/core/simulator/runtime_impl.py:887`, `app/core/simulator/tick.py:396` | △ гипотеза о триггере |
| F-034-11 | P2 | `payment-targets` в API (`simulator.py:2310-2336`): BFS на каждую цель, O(N·(N+E)), плюс max-flow на цель при `limit` до 1000; использует приватные `router._bfs_single_path` и `PaymentService._confine_router_to_perimeter`; GET-эндпоинты блока запускают запись сидинга (`_ensure_run_seeded`, `:2098`, `:2271`) | `app/api/v1/simulator.py:2310,2098,2271` | △ |
| F-034-12 | P3 | Мёртвый код: `broadcast_trust_drift_changed` (функция `trust_drift_engine.py:78-141` и метод `:733-751`, проверяется только `tests/unit/test_topology_changed_no_empty_payload.py`); `runtime_impl.subscribe` (`:741-770`, дубль `subscribe_with_status`); только тестами используются `RealRunnerImpl._compute_stress_multipliers` (189), `_invalidate_caches_after_inject` (557), `_init_trust_drift` (625), `_apply_trust_growth` (628), `_apply_trust_decay` (644), `count_active_runs` (`runtime_impl.py:295`); `_await_pending_clearing` (`tick.py:677-744`) и ветка `tick_clearing_already_running` (`:1231-1237`) недостижимы, потому что `_execute_clearing_with_timeout` всегда дожидается или отменяет задачу (`:1239-1273`) — подтвердить покрытием; цепочка инвалидации из четырёх уровней `cache_invalidator` → `inject_executor:194` → `InjectExecutor:1066` → `real_runner_impl:557` | перечислено | △ |
| F-034-13 | P3 | `tx-once`/`clearing-once` разрешены на реальном ране: `_ensure_run_accepts_actions` (`runtime_impl.py:488-496`) проверяет только терминальное состояние; сфабрикованные `tx.updated`/`clearing.done` (сумма по умолчанию `"10.00"`, `:664-674`) идут в тот же SSE-поток и `events.ndjson`, что и настоящие. Продуктовый вопрос П3 | `app/core/simulator/runtime_impl.py:488` | △ |
| F-034-14 | P3 | Мелочи: нормализация статуса расходится (сидер `frozen`→`suspended`, `real_scenario_seeder.py:196-199`; инъекция любой неизвестный → `active`, `inject_executor.py:560-562`); схема принимает типы событий `payment`/`clearing` (`scenario.schema.json:253`), раннер молча помечает их сработавшими (`real_runner_impl.py:326-327`); фикстуры при загрузке схемой не валидируются (`scenario_registry.py:282-297`); прямой `PaymentRouter._graph_cache.pop` (`cache_invalidator.py:14`, `trust_drift_engine.py:307`) вместо `invalidate_cache`; `total_debt` считает SUM по всему эквиваленту, а не по периметру рана (`tick.py:1436-1438`); SSE-поток подставляет случайные `evt_*` id (`simulator.py:2394,2427,2440,2453,2475`), повтор по ним невозможен; `NumberOrString = Union[float, str]` на денежных полях ребра (`schemas/simulator.py:41,50-52`, правка — `Contract: yes`); админские роуты без `response_model` (`:2986`, `:3034`); restart не сбрасывает `_real_fired_scenario_event_indexes`; гипотеза утечки подписки при отключении клиента до первой итерации генератора (`:2577`, `:2824`) | перечислено | △ |

### Simulator UI v2

| № | Severity | Находка | Evidence | Проверка |
|---|---|---|---|---|
| F-034-15 | P1 | HTTP-клиент без таймаута и без корреляционного id: `httpJson` (`http.ts:44-67`) — `fetch` без `AbortController`; `ApiError` несёт только `status` и `bodyText` (`:26-36`); в `src/` ни одного `request_id`/`X-Request-ID` (grep пуст; `AbortController` есть только в `useInteractMode.ts` и `useSimulatorRealMode.ts`, не в транспорте). Нарушены §9 и §12. Админка оба пункта уже решила: `admin-ui/src/api/realApi.ts:179-216,262-312` — образец. Скачивание артефакта — прямой `fetch` в обход `api/` (`useSimulatorRealMode.ts:1302`) | `simulator-ui/v2/src/api/http.ts:44` | ✓ |
| F-034-16 | P2 | Линия доверия в четырёх копиях состояния: `state.snapshot.links` (глубокий reactive, `useSimulatorApp.ts:520`), `layout.links` (raw), `snapshotTrustlines`/`answeredTrustlines` в `interact/useInteractDataCache.ts:101,132`, замороженная линия в `useWmEdgeDetail`. `demo/patches.ts:75-121` патчит две копии параллельно, кэш — третью (`:208-210`). Слияние — в `.vue` (`SimulatorAppRoot.vue:634-665`, тихий fallback на снапшот при пустом REST-массиве); комментарий `:667-680` сам называет это «третьей копией» (F-013-7). Нарушает §9 «если можно протестировать без UI» | `simulator-ui/v2/src/components/SimulatorAppRoot.vue:634` | ✓ слияние в `.vue`; число копий △ |
| F-034-17 | P2 | Три ручные копии контракта без сверки с `api/openapi.yaml`: `simulatorContracts.ts` 843 + `normalizeSimulatorEvent.ts` 569 + `simulatorTypes.ts` 411 + `types.ts` 118; ни один тест не сверяет их со спекой (`simulatorApi.contract.test.ts:115` — только комментарий). Из проверенных схем: `RunStatus` — `api_version`, `mode` обязательны в OpenAPI, optional в `simulatorTypes.ts:236-241`; `ScenarioSummary` — `participants_count`, `trustlines_count`, `equivalents` обязательны, optional в `:5-19`, лишние `label`/`mode`/`updated_at`. Дрейф в сторону ослабления, ловится только глазами. Общая с админкой часть — F-035-13 | `simulator-ui/v2/src/api/simulatorTypes.ts:236` | △ |
| F-034-18 | P2 | Оконный менеджер: `windowManager/**` 1508 + `useWindowController.ts` 589 + `useWmEdgeDetail.ts` 291 + `WindowShell.vue` 386 = **2774** строки продукта; тесты 2283 строки, 92 `it`; плюс `legacyReference/` 280. Обслуживает 3 типа окон (`types.ts:16`) без drag/resize (`WindowShell.vue:203` только эмитит focus). **Поправка к 022:** `useLayoutCoordinator.ts` (616) — координатор раскладки графа, `useOverlayState.ts` (463) — FX-оверлеи; к WM не относятся, их удаление сломает FX. Что держится на WM: ESC-стек (`useWindowManager.ts:1083`), возврат фокуса (`:371-442`, a11y), `useDestructiveConfirmation.ts:3,65`, переиспользование node-card (`:835`), один селектор в `e2e/manual-operations-interact.spec.ts` | `simulator-ui/v2/src/composables/windowManager/` | △ числа по `wc -l` агента |
| F-034-19 | P2 | Корень склеен: `useSimulatorApp.ts` 1971 строка, возвращаемый `SimulatorAppApi` — 62 поля (`:125-291`), 4 тестовых шва `__*` в продуктовом модуле (`:292,303,332,377`); `SimulatorAppRoot.vue` 1650 (script 1–1271). Флаги режима из URL/env читаются внутри composable (`:425-520`); `isTestMode`/`webdriver` — 133 упоминания в 28 продуктовых файлах, 11 продуктовых файлов экспортируют `__*`/`__testing`. `useSimulatorRealMode.ts` 1468: SSE-транспорт с replay-recovery (`:589-1036`, ~450 строк) склеен с управлением раном | `simulator-ui/v2/src/composables/useSimulatorApp.ts`, `useSimulatorRealMode.ts` | △ |
| F-034-20 | P2 | Две модели физики: собственный Barnes–Hut на d3-quadtree (`layout/forceLayout.ts:171-450`, режим назван `'admin-force'`, `:13,711`) и d3-force для живой раскладки (`layout/physicsD3.ts`, 367). Разные равновесия между стартом и «оживлением» | `simulator-ui/v2/src/layout/forceLayout.ts:171` | △ |
| F-034-21 | P2 | Сравнение денег через float в подсказках и фильтрах: `ManualPaymentPanel.vue:87-104,219`, `TrustlineManagementPanel.vue:146-158`, `EdgeDetailPopup.vue:154-162` через `parseAmountNumber`; при точности до 8 знаков `exceedsCapacity` и `available > 0` могут ошибаться. Сервер решает сам, вред — в подсказке | `simulator-ui/v2/src/components/ManualPaymentPanel.vue:87` | △ |
| F-034-22 | P2 | Гард-тесты по тексту исходников: 22 из 119 test-файлов читают исходники через `readFileSync`, ≥85 ассертов `toContain/toMatch` по тексту (`useMetricsPolling.test.ts:890,957`, `useSimulatorApp.analyticsGate.test.ts`, `compactOverlayFormRails.test.ts` — 6 чтений). §11 разрешает только явно названные policy-гарды. Тесты честности `EdgeDetailPopup.*` не трогать (022 VP-4) | `simulator-ui/v2/src/**/*.test.ts` | △ нижняя оценка по regexp |
| F-034-23 | P3 | Глубокий reactive снапшот (`useSimulatorApp.ts:520`): все узлы и рёбра проксированы, патчи пишут через proxy (`demo/patches.ts:89,115`); отрисовка читает raw `layout` (`useRenderLoop.ts:376-388`), так что цена — только патчи и память. Сигнатура состава узлов — эвристика по 5 сэмплам (`useRenderLoop.ts:108-122`). localStorage в обход объявленного единого слоя: `useSimulatorApp.ts:503-518` (7 `lsSet`), `useFxDebugControls.ts:76,104` при декларации «TD-1: all localStorage access is delegated» (`SimulatorAppRoot.vue:40`) | перечислено | △ |

## Current / Intended / Optimal

- **Current.** Симулятор — клиент ядра по деньгам (021), но держит вторую копию оркестровки клиринга, сидинга и периметра в API-слое; строит визуальные патчи внутри денежной транзакции; при рестарте повторяет `tx_id`; UI-транспорт без таймаута и id; оконный менеджер и тройной контракт — цена сопровождения без пользовательской ценности.
- **Intended.** По 021 и `docs/ru/simulator/`: симулятор — демонстрационный клиент домена, все денежные эффекты — через публичные сервисы ядра, SSE — защищённый контракт (§8), runtime-артефакты — под `.local-run/simulator/` с TTL и лимитом (§12), логирование тика — счётчики, а не строки на итерацию (§12).
- **Optimal.** Денежная фаза тика содержит только деньги; патчи — после коммита на своей сессии (как уже делает клиринг); одна функция клиринга, одна `ensure_seeded`, один `run_perimeter` в core, хендлеры тонкие (`simulator.py` ≈1200–1500 строк); исполнитель — последовательный цикл; ключ идемпотентности несёт эпоху рестарта; UI — один транспорт с таймаутом и `request_id`, один источник выбранной линии с `figuresSource` в `useInteractDataCache`, два фиксированных дока вместо WM, типы из OpenAPI.

## Продуктовые вопросы владельцу

- **П1.** Продлить исключение «упрощение ядра — цель» (2026-09-24, покрывало 018–021) на симулятор, бэкенд и UI? Без него §19.5 не разрешает рефакторинг без воспроизведённой потери; F-034-1, F-034-2, F-034-15 проходят и без продления как дефекты честности экрана.
- **П2.** Инъекции сценария (F-034-6): заморозить (флаг и код остаются, новых задач нет), удалить (≈1500 строк рядом с деньгами) или дать продуктовый путь (фикстура с inject-событиями)? Пока решения нет — в коде не наращивать (§19.4).
- **П3.** `tx-once`/`clearing-once` на реальном ране (F-034-13): отказывать при `mode == "real"` или оставить (UI ими пользуется в fixtures-режиме)?
- **П4.** Оконный менеджер (F-034-18): заменить двумя фиксированными доками и хелпером ESC/фокуса (≈200 строк вместо 2774 + 2283 тестов) — это смена UX демо-экрана; подтвердить.

## Non-goals

- Денежная семантика, `app/core/payments/`, `clearing/`, `ledger/`, `money_boundary.py` не меняются. Публичные методы, нужные симулятору вместо приватных (`PaymentRouter` для `payment-targets`, классификация ошибок БД вместо `_drain_call`/`_public_error_of_stored`), запрашиваются у 035 как `Contract: yes` и реализуются там.
- SSE wire shapes, имена событий и replay-контракт не меняются без отдельного решения и тестов §8. Случайные `evt_*` id (F-034-14) — только если без смены контракта клиента.
- `simulator-ui/v1/` не удаляется этой программой — отдельный cleanup-slice с тегом (AGENTS §3, §14).
- Новых таблиц, миграций, фоновых процессов, фреймворков и слоёв нет. Генератор типов из OpenAPI вводится только совместно с 035 (F-035-13), одной зависимостью на оба UI.
- Админский фолбэк `SseEventEmitter._publish` для тестовых дублёров остаётся (решение 032).

## Проверка на переусложнение (§19.2)

Ответы на 2026-10-08; дополняются соседним агентом по мере уточнения объёма.

1. **Наблюдаемая потеря.** Денежной нет. Наблюдаемый вред: ложный `COMMITTED` на экране после рестарта (F-034-1), остановка рана по отравленной транзакции (F-034-2), бесконечный busy и ненаходимая ошибка в UI (F-034-15). Остальное — сопровождаемость; основание — П1.
2. **Протокол.** Не затрагивается: симулятор не владеет ни одним правилом протокола, он клиент (`docs/en/00-overview.md`, 021).
3. **Противник.** Не вводится защит против внутрипроцессного противника. F-034-2 — не защита, а соблюдение границы транзакции.
4. **Дешевле.** Новых сущностей нет: все правки — перенос существующих функций из API в core и удаление копий. Для F-034-1 дешевле всего — добавить эпоху рестарта в материал ключа (2–3 строки), альтернатива «новый `run_id` при рестарте» ломает контракт клиента. Для F-034-2 — построить патчи после коммита (клиринг уже так делает, `tick.py:1105-1168`), альтернатива «ретрай 25P02» — компенсация ниже по потоку, запрещена §9.
5. **Цена, числом.** Оценка агентов, уточняется: бэкенд — минус ≈300 строк из `simulator.py` (копии клиринга, сидинга, периметра), минус ≈150 (мнимая параллельность), минус ≈200 (мёртвое по F-034-12), ≈+60 (патчи после коммита), ≈+10 (логи дрейфа топологии), ≈+20 (TTL/лимит артефактов); UI — ≈+80 (транспорт), ≈−60/+40 (единый источник линии), ≈−2600 продукта и −2300 тестов (WM, при П4), ≈−400 (одна физика), ≈−600/+150 (типы из OpenAPI, с 035). Новых запросов к БД на операцию — ноль; на платёж тика — минус ≈4 внутри окна блокировки.
6. **Крайние случаи.** Берутся только с путём в приложении: рестарт рана с тем же seed; ошибка БД в построении патча; обрыв SSE и повтор; отключение клиента до первой итерации генератора (гипотеза F-034-14 — сначала репродьюсер). Не берутся: мультиворкер симулятора (один процесс по дизайну), более одного рана на владельца.

## Verification plan

**Репродьюсеры, обязанные падать на `b37f5ef8`:**

- **F-034-1.** Ран с детерминированным seed: тик 0 проводит платёж A→B на сумму X, `restart`, тик 0 снова планирует A→B на X; сейчас второй платёж возвращает тот же `tx_id` и статус `COMMITTED` из сохранённой строки при неизменных `debts` (сверка до/после по `Book`), после починки — новый `tx_id` и настоящий эффект (или честный отказ). Ассерт — на `tx_id` второго платежа и на дельту долга, не на лог.
- **F-034-2.** Слушатель `after_cursor_execute` на соединении денежной сессии тика: между `BEGIN` и `COMMIT` денежной транзакции **нет** запросов к `participants`/`trust_lines`/`equivalents`, сделанных построителями патчей (`VizPatchHelper`, `compute_node_patches`, `build_edge_patch_for_pairs`); сейчас есть. Второй ассерт: число запросов внутри окна `FOR UPDATE` до/после — записать числа в Changelog.
- **F-034-15.** Vitest: `httpJson` с зависшим `fetch` завершается `ApiError` по таймауту; ответ с `X-Request-ID` или `error.request_id` даёт `ApiError.requestId`, и `extractErrorMessage` включает `(ref: …)`. Сейчас — висит и `requestId` нет.
- **F-034-3/4.** После слияния: `grep -c "run_clearing_pass" app/api/v1/simulator.py` = 0 и `grep -c "_ensure_run_seeded\|build_graph_snapshot" app/api/v1/simulator.py` — только вызовы core-функций; гард-тест формы допускается как явно названный policy-гард (§11) с сообщением, что именно и где проверять.
- **F-034-8.** Тест: при `TTL>0` и лимите N после `finalize` рана N+1 каталогов → остаётся N, чистка вызвана после записи, а не в конструкторе; переполнение очереди событий увеличивает счётчик, видимый в `run_status`/метриках.
- **F-034-16.** Vitest без mount: `useInteractDataCache` отдаёт `selectedLink` и `figuresSource` для выбранной пары; REST-ответ пуст → `figuresSource = snapshot`; непуст → `rest`. Корень потребляет одну структуру (тест-мутант: удалить ветку fallback в `.vue` — после переноса ветки там нет).

**Инварианты, обязанные выжить, и контрпроверки:**

- Денежные тесты ядра и симулятора без правки ассертов: `tests/unit/test_simulator_real_planner_determinism.py`, `test_simulator_real_amount_model.py`, `test_flow_and_periodicity.py`, `test_warmup_and_capacity.py`, стенды 021 (`tests/p021_support.py` и `test_p021_*`), стенды 030 S3 (сверка транзакций симулятора), `test_p024_*` по симулятору.
- Детерминизм плана: `plan_payments` по 20 сидам × сценарии × тики 0–9 × интенсивности — побайтно равен до/после, и суммарное число планов > 0 (anti-vacuum, как в 032 S3).
- SSE: `tests/unit/test_topology_changed_no_empty_payload.py` и прочие тесты wire shapes зелёные без правки; `simulator-ui/v2` `normalizeSimulatorEvent.*.test.ts` зелёные; `ui-smoke` и `test:e2e` симулятора зелёные.
- Anti-vacuum для удалений (F-034-12): для каждой удаляемой функции — grep по `app/` и `scripts/` = 0 вызывающих, и тест, проверявший её, удаляется с доказательством §11 или переписывается на живой путь.
- Исполнитель после F-034-5: число проведённых платежей за тик и порядок `tx.updated` совпадают до/после на фиксированном seed.

**Существующие селекторы, остающиеся зелёными:** перечисленные выше плюс `tests/integration/test_simulator_super_smoke.py` (`-IncludeExpensive`), `tests/integration/test_simulator_real_snapshot_db_enrichment.py`, `tests/unit/test_simulator_*`, `tooling-tests/portable/test_p029_s4_demo_fixtures_of_every_equivalent_are_current.py`; `simulator-ui/v2`: `money.conformance.test.ts`, `EdgeDetailPopup.*`, `useInteractDataCache.*`, `scenes.spec.ts` (визуальные снимки меняются только при П4/F-034-20 с ручной проверкой).

**Запрещено:** ретрай или подавление `25P02` как починка F-034-2 (компенсация ниже по потоку); новый `run_id` при рестарте как починка F-034-1 без решения о контракте клиента; удаление теста без доказательства §11; доказательство «поведение не изменилось» чтением диффа вместо прогона детерминизма; `sleep` вместо управляемых часов; правка SSE wire без тестов §8; ослабление `.passthrough()` → строгих схем без сверки с OpenAPI (дрейф должен стать видимым, а не исчезнуть молча).

**Гейты каждого среза:** `.\scripts\verify_local.ps1 -TaskSlug p034<s> -BackendOnly` (полный тир) для бэкенд-срезов; `-UiOnly` для UI; `-ToolingOnly` при правке фикстур; `tests/integration/test_simulator_super_smoke.py -IncludeExpensive` как milestone; Ruff; CI-jobs `required-backend`, `required-ui`, `ui-smoke`, `static-diagnostics` на SHA PR по имени и итогу; `simulator-super-smoke` и `simulator-visual-e2e` — `workflow_dispatch` на HEAD среза перед закрытием.

## Внешнее ревью

Триггеры §15: транзакционные границы рядом с деньгами (F-034-2, F-034-5), ключ идемпотентности (F-034-1), SSE при любой правке wire, закрытие программы. Консультация `T3400` до реализации; §15-ревью Codex каждого среза на точном HEAD; один fix-delta; классификация §19.5. Закрывающее ревью по §19.5.

## Срезы (предварительно; порядок и состав уточняет соседний агент и консультация `T3400`)

- **S1 — честность рана (бэкенд).** F-034-1, F-034-2, F-034-9, F-034-10. Малые правки с репродьюсерами; идут первыми и не зависят от П1.
- **S2 — одна копия в core.** F-034-3, F-034-4, F-034-11 (с публичным методом `PaymentRouter` из 035). `simulator.py` → тонкие хендлеры.
- **S3 — исполнитель и мёртвое.** F-034-5, F-034-12, F-034-7, F-034-8, F-034-14 (кроме `Contract: yes`).
- **S4 — инъекции** — только после П2.
- **S5 — UI транспорт и источник линии.** F-034-15, F-034-16, F-034-21, F-034-23. Не зависит от П1 в части F-034-15.
- **S6 — UI структура.** F-034-19, F-034-20, F-034-22, F-034-17 (совместно с 035 F-035-13).
- **S7 — оконный менеджер** — только после П4.

## Tasks

Легенда: `[x]` выполнено, `[ ]` в работе или ожидает, `[!]` заблокировано или не авторизовано.

| ID | Задача | Статус |
|---|---|---|
| `T3400` | Консультация Codex по спеке: объём, порядок срезов, перенос симуляторной части 022 в 034, маркеры `VERDICT-034-SPEC`, `SCOPE-034`, `CLASS-1-COUNT`, `READY-TO-IMPLEMENT`; вердикт и ledger — в Changelog | `[ ]` |
| `T3401` | Репродьюсеры F-034-1, F-034-2, F-034-15 исполнены на `b37f5ef8` и красны; числа записаны | `[ ]` |
| `T3402` | Подтверждение △-находок F-034-3…F-034-14, F-034-16…F-034-23 чтением с `path:line` (соседний агент); снятые — вычеркнуть с причиной | `[ ]` |
| `T3403` | Ответы владельца П1–П4 внесены | `[!]` |
| `T3410` | S1 — честность рана; §15-ревью | `[!]` |
| `T3420` | S2 — одна копия клиринга, сидинга, периметра в core; §15-ревью | `[!]` |
| `T3430` | S3 — исполнитель, логи, артефакты, мёртвое | `[!]` |
| `T3440` | S4 — инъекции по П2 | `[!]` |
| `T3450` | S5 — UI транспорт, источник линии, деньги без float | `[!]` |
| `T3460` | S6 — UI структура, типы из OpenAPI (с 035) | `[!]` |
| `T3470` | S7 — оконный менеджер по П4 | `[!]` |
| `T3490` | Закрывающее ревью §19.5 на HEAD программы; BACKLOG; реестр; пометка в 022 о переезде симуляторной части | `[!]` |

## Changelog

- **2026-10-08** — спека заведена по слову владельца («Симулятор будем рефакторить отдельно — заведи на него спеку, которую будет дополнять соседний агент»). Источник — аудит пяти read-only агентов (Opus) на `b37f5ef8`: бэкенд симулятора (97 вызовов инструментов), оба фронтенда (82), плюс перепроверка оркестратором якорей F-034-1 (`run_lifecycle.py:496`, `real_runner_impl.py:693`, `payments/service.py:1079`), F-034-2 (`real_payments_executor.py:703,733`), F-034-15 (`http.ts:44-67`, grep `request_id` пуст), F-034-16 (`SimulatorAppRoot.vue:634-680`). Остальные находки помечены △ и ждут `T3402`. Тесты и гейты в рамках аудита не запускались. Сестринская программа — [035](../035-post-audit-continuation/spec.md) (ядро, Admin UI, тесты, документация).
