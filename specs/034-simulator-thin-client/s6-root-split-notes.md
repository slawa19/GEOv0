# 034 S6 — Корень Simulator UI: находки и идеи разделения (описание, не план работ)

- **Date:** 2026-10-16, против `main` на `eac53be8`.
- **Статус:** **описание по слову владельца 2026-10-16** — «разделить корень хочу, надо описать находки и идеи, но пока не реализовывать. После тестирования руками (может, я бы убрал какой-то функционал) следует разделить». Реализация `T3460` остаётся `[!]` до ручного теста владельца и его решения, что из функционала убрать.
- **Источник фактов:** инвентаризация read-only агентом (Opus) 2026-10-16; якоря `__*`-швов (`:342,353,382,427`), список тем (`TopBar.vue:319-322`), раскладки (`BottomBar.vue:153-159`) и гард по тексту исходника (`useSimulatorApp.analyticsGate.test.ts:44`) перепроверены советником. Числа F-034-19 в `spec.md:53` (1971 / 1650 / швы `:292,303,332,377`) устарели — актуальные ниже.
- **Как читать владельцу:** раздел 3 — что видно на экране, простыми словами, для ручного теста; раздел 6 — что можно убрать (только список, без рекомендаций); разделы 1, 2, 4, 5 — для оркестратора, когда дойдёт до разделения.

## 0. Что такое «корень» и почему он один

Экраном симулятора управляют три слоя: `src/composables/useSimulatorApp.ts` (2 027 строк), `src/components/SimulatorAppRoot.vue` (1 624: script 1–1216, шаблон 1218–1571, стили дальше) и тонкие фабрики `src/composables/useApp*.ts` (по 42–127 строк, вместе ≈ 1 000; своего общего состояния у них нет). В первом файле собственной логики ≈ 900 строк, типов и сборки ≈ 360, остальное — проводка. Все провода сходятся в одну коробку: правка в любой группе требует прогона всего экрана.

## 1. Карта ответственностей `useSimulatorApp.ts`

| Группа | Строки (≈) | Что читает / пишет | С кем связана |
|---|---|---|---|
| A импорты | 1–80 | — | 40+ модулей |
| B загрузчики снимка рана (`loadStrictRunRecoverySnapshot`, `loadActiveRunSnapshot`) | 83–153 | API | источник сцены R, тесты runSnapshot/requestRef |
| C тип `SimulatorAppApi` (~65 полей) | 155–329 | — | Root деструктурирует ~50 полей (`SimulatorAppRoot.vue:354-440`) |
| D тест-швы и политики `__*` | 331–437 | чистые | см. §4 |
| E опции от корня (колбэки окон, флаг аналитики) | 439–470 | — | обратный вызов в Root → windowController |
| F флаги режима из URL/env (`apiMode`, `isDemoUi`, `isInteractUi`, `isTestMode`, `isWebDriver`, `isE2eScreenshots`, `isFxDebugEnabled`) | 472–567 | `window.location`, `import.meta.env`, `navigator.webdriver` | читаются почти всеми; `isTestMode`/`isWebDriver`/`isE2eScreenshots` — 40 вхождений |
| G состояние ранов и персист (`state` 588, `real` 600, шесть `watch` → localStorage 712–739) | 569–739 | пишет `real.*`, `geo.sim.v2.*` | cookie-bootstrap 663, admin-runs 673 |
| H выбор узла, DOM-ссылки, офлайн-действия e2e | 741–776 | `state.selectedNodeId`, `state.snapshot` | FX-оверлеи |
| I цвета данных и темы (`vizMapping`, `dataPalette`) | 778–803 | токены CSS хоста | Root при mount и смене темы (`:449-450`) |
| J преференции вида (`labelsLod`, `quality`, `effectiveEq`) | 805–851 | `real.scenarios`, `state.snapshot` | `usePersistedSimulatorPrefs` |
| K Interact (`useInteractActions`, `useInteractMode`, `systemBalance`, заглушки поздней привязки) | 853–922 | `real.runId` через сеттер | FX клиринга, realMode |
| L раскладка, камера, индексы, `useAppViewWiring`, `useAppFxOverlays` | 924–1062 | `layout` (не reactive), `camera`, `state.flash` | рендер, пикинг, физика |
| M цикл рендера и тики (`useAppRenderLoop`, `*Safe`-геттеры) | 1064–1183 | `fxState`, `state.flash` | физика и drag привязываются позже (1267, 1444) |
| N перф-оверлей `?perf=1` | 1185–1238 | `fxState.__lastFps` | `DevPerfOverlay` |
| O патчи, физика, pin, FX транзакций (`useRealTxFx`) | 1240–1311 | `layout`, `pinnedPos` | realMode |
| P подписи, пикинг, hover | 1313–1358 | `labelsLod`, `camera`, `hoveredEdge` | — |
| Q клики по холсту, Step 0, двойной клик, drag-to-pin, клик по ребру | 1360–1481 | `state.selectedNodeId`, `interactMode` | колбэки окон E, `window.confirm` |
| R источник сцены (`runSceneLoads`, `requestedSceneSource`, `loadSnapshotForUi`, `useAppSceneState`) | 1486–1612 | `real.runId`, `runStatus`, `desiredMode`, `state.sourcePath` | realMode через позднюю привязку |
| S подключение realMode (`useSimulatorRealMode`, 1 485 строк: SSE, ран, артефакты), intensity | 1614–1686 | пишет `real.intensityPercent` в двух местах | все FX |
| T FX Debug, авто-старт рана для Demo и Interact | 1688–1767 | `state.loading`, `real.runId`; `state.error` через `__autoBootstrapMaybeFillUiError` | `useInteractAutoBootstrapRun` |
| U dev-hook (`window`) и жизненный цикл | 1769–1817 | пишет `hoveredEdge.*` напрямую (1781–1790) | колбэки окон |
| V аналитика: гейт видимости и `useMetricsPolling` | 1819–1843 | флаг панели из Root | `RealMetricsPanel` |
| W сборка объекта `api` | 1845–2027 | — | — |

## 2. `SimulatorAppRoot.vue`

**Script делает сам, помимо вызова composable:** тема (43–84, 450: `?theme=`, storage, `popstate`; четыре темы `hud | shadcn | saas | library`, `types/uiPrefs.ts:11`); зачистка легаси-параметра `devtools` (90–100, 693); геометрия окон и HUD-полос через ResizeObserver и CSS-переменные (102–138, 236–352, 611–639); мост к оконному менеджеру модульными `let` и `queueMicrotask` (179–203, 496–509); флаг и док панели аналитики (213–225, 856–923); provide/inject трёх контекстов, ~30 computed над `real`/`admin` (571–603); окно ребра keep-alive и выбранная линия (519–551, 648–661); переключение режимов **перезагрузкой страницы** (`goSandbox/goAutoRun/goInteract`, enter/exit Demo со снимком storage, 689–1028); demo-кнопки с записью `real.lastError` (663–687); обработчики Interact из окна ребра, карточки и ActionBar (1032–1176); возврат фокуса после потока пятью модульными `let` (824–845, 933–958, 1178–1215); навигатор графа и «узкие места» → камера (812–931).

**Шаблон (1218–1571):** два canvas и drag-preview; `EdgeTooltip`; `TopBar` со слотом (`SystemBalanceBar`, `ActionBar` только в Interact, `GraphNavigator`); окна через `WindowShell` по `wm.windows` (`EdgeDetailPopup`, `NodeCardOverlay`, `ManualPaymentPanel`, `TrustlineManagementPanel`, `ClearingPanel`); мини-панель для e2e-скриншотов либо `BottomBar` со слотом `Legend`; док `RealMetricsPanel`; оверлеи Loading/Error; `LabelsOverlayLayers`, `DevPerfOverlay`; тосты и `InteractHistoryLog`. Шаблон сам пишет `interact.mode.*` при dismiss (1554, 1560).

## 3. Что видит пользователь — простыми словами (для ручного теста)

**Верхняя полоса** (`components/TopBar.vue`):
1. **Sandbox / Auto-Run / Interact** (284–309) — три режима; каждый переключается перезагрузкой страницы. Sandbox — демо-граф без сервера; Auto-Run — сервер сам гоняет платежи; Interact — платежи вручную.
2. **Theme** (312–325) — четыре темы оформления; по 039 тема `shadcn` должна уйти из списка, в коде пока есть (319).
3. Бейдж **TEST MODE**.
4. **Scenario** и **Refresh scenarios** (334–350) — выбор и обновление списка сценариев сети.
5. **Start / Pause / Resume / Stop** (355–388) — управление раном.
6. **Advanced ⚙** (390–450) — Pipeline (данные из БД или только топология) и Intensity в процентах с Apply.
7. Статусы: SSE, состояние рана, причина остановки, счётчик TX (459–490).
8. **Admin ▾** (488–590) — список всех ранов, «Stop all», Attach и Stop для каждого.
9. Плашка ошибки и предупреждение «Capacity stall» (597–610).

**Под верхней полосой** (`SimulatorAppRoot.vue:1279-1309`): 10. **SystemBalanceBar** — общий баланс и долг; 11. **ActionBar** — Send Payment, Manage Trustline, Run Clearing (только Interact); 12. **GraphNavigator** — список узлов и рёбер, клик открывает карточку или окно ребра.

**Холст:** 13. наведение на ребро — подсказка с суммами; 14. клик — выбор узла (в Interact — выбор участника в потоке; клик в пустоту отменяет поток); 15. двойной клик — карточка узла; 16. перетаскивание закрепляет узел, колесо — масштаб; 17. клик по ребру в Interact открывает окно ребра.

**Окна** (не больше двух групп): 18. **карточка узла** — статистика рёбер, Pin/Unpin; в Interact — «отправить платёж», «новая линия», «изменить линию»; 19. **окно ребра** — лимит, использовано, доступно; Change limit, Close line, Send payment; 20. **панель платежа**, **панель линии доверия** (полный редактор), **панель клиринга**.

**Нижняя полоса** (`components/BottomBar.vue`): 21. **EQ** — эквивалент (145); 22. **Layout** — пять раскладок: Organic, Clusters, Balance, Type, Status (153–159); 23. **Scene** — только Sandbox, демо-сцены (164); 24. **Quality** Low/Med/High (171); 25. **Labels** Off/Selection/Neighbors (180); 26. **Refresh** снимка (190); 27. **Reset view** (199); 28. **Analytics** — правая панель метрик и «узких мест» с переходом к ребру (208); 29. **Artifacts ▾** — скачать файлы рана, только Auto-Run (219–262); 30. **Dev Tools ▾** — Enter/Exit Demo; в Demo — Single Tx и Run Clearing (265–313); 31. **Legend** — цвета данных (слот 1489).

**Прочее:** 32. тосты успеха/ошибки и журнал Interact (1551–1569); 33. скрытые режимы через URL: `?perf=1` (FPS), `?debug=1` (FX Debug), `?ui=demo`, `?theme=`, `?e2eReal=1`.

## 4. Что мешает разделению

- **Общие изменяемые объекты.** `state` (`:588`) пишут `selectNode` 741, `setFlash` в трёх местах (1053, 1087, 1157), `useAppSceneState`, `useSimulatorRealMode`, `__autoBootstrapMaybeFillUiError`. `real` (`:600`) пишут шесть `watch`-персистов, сеттер `interactRunId` (856–862), intensity (1658–1686), `realActions`, realMode, Root (`real.lastError`, `SimulatorAppRoot.vue:681,685`); TopBarContext читает его 15 computed. `hoveredEdge` мутирует dev-hook (1781–1790); `interact.mode.successMessage` и `state.error` мутирует шаблон Root (1554, 1560).
- **Двусторонние зависимости через позднюю привязку** (`let …Impl/…Safe`, переназначаемые ниже): `getCameraZoomSafe` 826→1028; `resetStaleRunOwner` 863→1654; FX-заглушки 883–893→1164–1166; `wakeUpImpl` 928→1131; drag/физика 1067–1071→1267/1444; `handleRealSceneContextChange` 1570→1653. Циклы: render ↔ физика/drag, сцена ↔ realMode, interact ↔ FX, interact ↔ realMode.
- **Цикл Root ↔ composable:** Root передаёт `uiOpenOrUpdate*`, composable возвращает `getNodeScreenCenter`, нужный `windowController` (`SimulatorAppRoot.vue:182-203`, 496–507). Модульные синглтоны в Root: `windowController`, `getHostEl`, `interactFlow*`, provide-контексты. В composable-слое синглтонов уровня модуля нет.
- **Тест-швы, импортируемые тестами напрямую:** `__analyticsPanelVisibilityPolicy` (`useSimulatorApp.analyticsGate.test.ts:6`), `__closeTopmostOverlayOnOutsideClickPolicy` и `__selectNodeFromCanvasStep0` (`windowManagementStep0.test.ts:3`), `__autoBootstrapMaybeFillUiError` и `loadStrictRunRecoverySnapshot` (`auto-bootstrap-errors.test.ts:4`, `requestRef.test.ts:13`); вне корня — `__clampRealTxTtlMs`, `__pickSparkEdges`, `__testing` в `fxRenderer`/`glowSprites`/`gradientCache`.
- **Гарды по тексту исходника** (ломаются при любом переносе): `useSimulatorApp.analyticsGate.test.ts:44` и `RealMetricsPanel.cameraFocus.test.ts:220` читают `useSimulatorApp.ts` через `readFileSync` — попадают под 041 F-041-9.
- **Мок целиком:** `SimulatorAppRoot.interact.test.ts` (5 887 строк, 101 mount) мокает `useSimulatorApp`, `useWindowManager`, `usePersistedSimulatorPrefs` — покрывает Root, не composable; любая смена формы `SimulatorAppApi` правит мок. Тесты composable в `effectScope` с 6–30 `vi.mock` дочерних модулей привязаны к путям импортов из `useSimulatorApp.ts`: перенос подключения дочернего модуля в другой файл ломает `vi.mock`.

## 5. Идеи разделения без переписывания (кандидаты, порядок — от дешёвого к дорогому)

| # | Модуль | Что переносится | Общее остаётся | Выгода | Риск | Покрытие, которое это держит |
|---|---|---|---|---|---|---|
| 1 | `simulatorModeFlags.ts` | группа F (≈95) + `showPerfOverlay`, `isFxDebugEnabled` (это п. 7 спеки, `spec.md:125`) | потребители получают computed | правила URL/env и режимов тестов не трогают файл приложения | низкий | `e2e/scenes.spec.ts`, `phase5-functional-smoke.spec.ts`, mount-тест Root |
| 2 | `realRunState.ts` | `real`, `state`, персист, `resetRunStats`, сценарий по умолчанию (588–739, ≈150) + intensity Interact (1658–1686) | сами объекты `real`/`state` | ключи localStorage и дефолты правятся отдельно | средний: много писателей | `stale-runid-first-load.spec.ts`, `unknownRunStatus.test.ts`, `TopBar.wiring.test.ts` |
| 3 | `sceneSource.ts` | группа B + `loadSnapshotForUi`, `runSceneLoads`, `requestedSceneSource` (≈180; «источник снимка» из спеки + типизированный `SceneSource`) | `real`, `state.sourcePath` | откат «ран → превью», таймауты и восстановление после SSE не трогают рендер и окна | средний: поздняя привязка `resetStaleRunOwner`, моки по путям | тесты `runSnapshot`, `sceneLoadFailure.p034`, `unknownRunStatus`, `attachTimeout`; Playwright `scenario-switch-real-mode`, `stale-runid-first-load` |
| 4 | `canvasScene.ts` | группы L, M, O, P и часть Q (924–1481, ≈550): раскладка, камера, рендер, физика, drag, FX, подписи, пикинг; `*Safe`-заглушки остаются внутри одного модуля | `state.snapshot`, `selectedNodeId`, `quality`, `labelsLod`, `vizMapping`, колбэки клика | правки рендера/физики/FX не открывают файл ранов и окон; циклы поздней привязки локализуются | самый крупный: интерфейс ≈30 полей, `getDimmedNodeIds` тянет `interactMode` | `e2e/scenes.spec.ts`, `e2e/qa/**` (drag, motion, label-fade, data-colours), `useAppDragToPinAndPreview.p039.test.ts`, `useAppFxOverlays.test.ts`, `useAppViewWiring.test.ts` |
| 5 | `demoAndDevTools.ts` | группы H, N, T, U (≈230) + из Root `enter/exit/toggleDemoUi`, `runDemoFxOnce` (663–727, 960–1001) | `real`, `state`, `addActiveEdge/Node`, `wakeUp` | всё тестовое и демо отделено от продукта (часть F-034-19 про `isTestMode`) | Demo-переход использует storage-снимки | `demoBootstrap.p034.test.ts`, `auto-bootstrap-errors.test.ts`, `dev/geoSimDevHook.test.ts`, мини-панель e2e |
| 6 | Root → `useRootChrome` + `useInteractFlowOrchestration` | (а) тема, геометрия HUD, ResizeObserver (≈280); (б) потоки Interact, якоря, возврат фокуса, keep-alive окна ребра (≈400) | `wm`, `wmEdgeDetail`, `interact.mode` | правки фокуса/потоков не задевают тему и раскладку, и наоборот | модульные `let`, порядок `queueMicrotask`; `tlPanel` остаётся в Root | `SimulatorAppRoot.interact.test.ts`, `manual-operations-interact.spec.ts`, `p037-*.spec.ts`, `useWindowManager.test.ts` |
| 7 | `useAnalyticsSurface` | группа V + флаг, док, `focusBottleneckTarget` из Root (213–225, 856–923) | — | аналитика в одном месте | текстовые гарды `analyticsGate`/`cameraFocus` переписать (F-034-22 — и так их судьба) | `RealMetricsPanel.cameraFocus.test.ts` (mount-часть), `useMetricsPolling.test.ts` |

Оконный менеджер (`windowManager/useWindowManager.ts` 1 161, `useWindowController.ts` 588, `useWmEdgeDetail.ts` 291) уже вынесен и связан с корнем только через Root; 034 говорит «не трогать в этой волне» (`spec.md:125`). Предпосылка любого переноса — снять гарды по тексту исходника (041 F-041-9) и перевести тесты composable с `vi.mock` по путям на mount/DOM, иначе каждый перенос красит зелёные тесты без изменения поведения.

## 6. Кандидаты на решение владельца после ручного теста — только список

- **Темы:** четыре в переключателе (`TopBar.vue:319-322`); по 039 `shadcn` замораживается (`039/spec.md:27`), но в UI пока есть; тема хранится в URL, storage и `popstate`.
- **Demo UI** (`?ui=demo`, Enter/Exit Demo, авто-старт рана, Single Tx и Run Clearing): `useSimulatorApp.ts:1700-1733`, `SimulatorAppRoot.vue:663-727,960-1001`, `BottomBar.vue:265-313`. Частично дублирует ActionBar в Interact.
- **FX Debug** `?debug=1` (`useSimulatorApp.ts:536-549,1688-1698`); **перф-оверлей** `?perf=1` (1185–1238).
- **Офлайн-пайплайн e2e-скриншотов** (`isE2eScreenshots`, `e2eTxOnce`: 494, 753–776; Root 1447–1454).
- **Admin-меню ранов** (Attach/Stop чужих ранов): `TopBar.vue:488-590`, `useSimulatorApp.ts:1894-1912`.
- **Pipeline «sandbox (topology-only)»** в Advanced (`TopBar.vue:415-424`) — пересекается с режимом Sandbox; обходы `forceDesiredModeReal` (`SimulatorAppRoot.vue:699-713`). Связано с режимом `fixtures` бэкенда (041 `T4191`).
- **Пять раскладок** (`BottomBar.vue:153-159`) и две модели физики (F-034-20).
- **Artifacts** (только Auto-Run) и **Refresh** снимка.
- **Два редактора линии** — быстрое окно ребра и полная панель с флагом `useFullTrustlineEditor` (`SimulatorAppRoot.vue:491,1032-1049,1178-1187`).
- **Легаси-остатки:** зачистка `devtools` в URL (90–100, 693); `mode=demo` → fixtures (`useSimulatorApp.ts:513,520`); сброс intensity 0 «после legacy Interact» (1656–1660); `WindowShell @close` «мёртвый в frameless» (`SimulatorAppRoot.vue:1323-1334`); каталог `src/legacyReference/` (только снимки разметки — 041 F-041-9); `useOverlayState.ts` (по 022 — оболочка); `simulator-ui/v1` (041 F-041-8); страница `design-system-demo.html` с `dev/DesignSystemDemoApp.vue`.
- **Из 022** (`022/spec.md:21`): оконный менеджер ≈4 300 строк и 76 тестов на CSS/разметку названы кандидатами на снятие; 034 и 039 опираются на WM как на стабильный (вариант A компоновки — «максимум два окна»). Решение остаётся открытым до ручного теста.

## Что дальше

1. 039 B–E закрываются поверх текущего корня (решение владельца: сначала картинка).
2. Тур 034 S9.
3. Владелец смотрит демо руками, отмечает по разделу 3, что убрать или изменить.
4. Только затем оркестратор ставит `T3460` как последовательность переносов из раздела 5, начиная с того, что освобождает место под замечания владельца; каждый перенос — отдельный PR с mount/DOM/Playwright-покрытием из таблицы, без новых гардов формы.
