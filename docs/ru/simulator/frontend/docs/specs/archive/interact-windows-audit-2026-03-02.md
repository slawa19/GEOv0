# Комплексный аудит поведения окон с карточками в Interact mode

> **Статус:** ARCHIVED (2026-03-06)
> **Исходный документ:** `docs/ru/simulator/frontend/docs/specs/interact-windows-audit-2026-03-02.md`
> **Причина архивирования:** аудит сверён с текущей реализацией; обязательные этапы перенесены в код, связанные спеки/план закрыты и сами уже архивированы.

## Что было перепроверено

Проверка выполнялась по текущему runtime `simulator-ui/v2` и gate-проверкам:
- `npm --prefix simulator-ui/v2 run typecheck` ✅
- `npm --prefix simulator-ui/v2 run test:unit` ✅

## Подтверждённые закрытия по коду

### UX-3 — closing state вместо debounce
Реализация подтверждена:
- `WindowLifecycleState = 'open' | 'closing'` и transition-aware close введены в `useWindowManager`.
- `handleEsc()` пропускает окна в состоянии `closing`.
- Есть unit/integration тесты на rapid double ESC.

Код:
- `simulator-ui/v2/src/composables/windowManager/types.ts`
- `simulator-ui/v2/src/composables/windowManager/useWindowManager.ts`
- `simulator-ui/v2/src/composables/windowManager/useWindowManager.test.ts`
- `simulator-ui/v2/src/components/SimulatorAppRoot.interact.test.ts`

### UX-7 / UX-8 — watcher-driven upsert без нежелательного focus
Реализация подтверждена:
- `wm.open()` поддерживает `focus: 'auto' | 'always' | 'never'`.
- watcher/controller-driven обновления используют `focus: 'never'`.
- user-initiated открытия (`node-card`, manual edge-detail open) используют `focus: 'always'`.
- z-order jump покрыт тестами.

Код:
- `simulator-ui/v2/src/composables/windowManager/types.ts`
- `simulator-ui/v2/src/composables/windowManager/useWindowManager.ts`
- `simulator-ui/v2/src/composables/useWindowController.ts`
- `simulator-ui/v2/src/composables/useWmEdgeDetail.ts`
- `simulator-ui/v2/src/components/SimulatorAppRoot.interact.test.ts`

### ARCH-2 / ARCH-3 / PERF-3 / RACE-3 — window bridging вынесен в controller
Реализация подтверждена:
- `useWindowController()` существует и держит `desiredWindows` + единый apply-diff watch для interact/edge-detail bridging.
- `SimulatorAppRoot.vue` использует controller как wiring-layer.
- Отдельная архивная спека по декомпозиции переведена в `ARCHIVED`.

Код/доки:
- `simulator-ui/v2/src/composables/useWindowController.ts`
- `simulator-ui/v2/src/components/SimulatorAppRoot.vue`
- `docs/ru/simulator/frontend/docs/specs/window-controller-decomposition-spec.md`
- `docs/ru/simulator/frontend/docs/specs/archive/window-controller-decomposition-spec.md`

### RACE-2 — busy-gate для ESC / outside-click
Реализация подтверждена:
- при `interact.mode.busy` глобальный ESC и hard-dismiss по outside-click проходят через confirm-gate;
- при подтверждении вызывается `cancel()` и закрываются соответствующие окна;
- при отказе пользователя ничего не закрывается.

Код:
- `simulator-ui/v2/src/composables/useWindowController.ts`
- `simulator-ui/v2/src/components/SimulatorAppRoot.interact.test.ts`

## Связанные документы со статусом DONE/ARCHIVED

- `plans/refactor-implementation-order-plan-2026-03-04.md` → stub на архив
- `plans/archive/refactor-implementation-order-plan-2026-03-04.md` → execution complete
- `docs/ru/simulator/frontend/docs/specs/window-controller-decomposition-spec.md` → stub на архив
- `docs/ru/simulator/frontend/docs/specs/archive/window-controller-decomposition-spec.md` → DONE

## Что остаётся как accepted / post-MVP debt

Это не блокирует архивирование аудита как рабочего документа:
- UX-1: полное устранение first-frame resize jump остаётся как post-MVP polish; в runtime есть partial fix (`min-height`/`contain`/phase-aware constraints), но не offscreen pre-measure.
- Focus-trap остаётся отдельным TODO; focus-return уже реализован.
- Bottom-sheet для узких экранов остаётся POST-MVP.

## Вывод

Документ выполнил роль рабочего аудита и больше не нужен как активная спека. Актуальные решения уже зафиксированы в коде, тестах и связанных архивных спеках/плане.
