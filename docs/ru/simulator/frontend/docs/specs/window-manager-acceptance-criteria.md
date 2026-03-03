# WindowManager (Simulator UI v2) — Acceptance Criteria + Implementation Map

Дата: 2026-03-01

Источник требований:
- `plans/archive/simulator-window-management-audit.md` (нормативный документ рефакторинга)

Цель этого файла:
- Перевести MUST/SHOULD требования в **проверяемые** критерии приёмки.
- Дать “карточку соответствия” (где в коде реализовано / где расходится).
- Зафиксировать трассировку критических событийных потоков real-mode (в частности `tx.updated`).

---

## 0) Термины (коротко)

- **Window**: overlay-сущность с собственной геометрией и жизненным циклом.
- **Group**: правило единственности окон по классу (`interact`, `inspector`).
- **UI-close**: закрыть окно как UI-слой (ESC или [×]).
- **Flow-cancel**: отменить бизнес-процесс Interact FSM.

---

## 1) Acceptance Criteria (AC)

### AC-1. Единственный источник правды — WindowManager
- MUST: открыто/закрыто, z-order/active, размещение и reclamp контролируются WindowManager.
- MUST: окно-контент не решает, закрывать ли другие окна.

Проверка:
- Открыть несколько окон в одной группе, кликать по ним → верхним становится последнее сфокусированное (порядок меняется **внутри группы**).
- Убедиться, что ESC действует на topmost (active) окно.


### AC-2. Группы и coexistence
- MUST: в группе `interact` одновременно не более одного окна.
- MUST: в группе `inspector` одновременно не более одного окна (NodeCard XOR EdgeDetail).
- MUST: `interact` и `inspector` могут сосуществовать.
- MUST: при coexistence действует приоритет слоёв: `interact` **всегда выше** `inspector` (даже если инспектор сфокусировали кликом). Фокус/active меняет порядок только внутри группы.

Проверка:
- Открыть NodeCard, затем запустить Manual payment → NodeCard остаётся открытым под панелью.
- Открыть EdgeDetail, затем открыть Trustline panel → EdgeDetail остаётся под панелью.


### AC-3. ESC как back-stack, с form-guard
- MUST: ESC работает как back-stack по окнам (topmost first), учитывает `escBehavior`.
- MUST: если фокус в input/textarea/select, ESC не должен ломать нативное поведение / отменять flow.

Проверка:
- При открытых `interact` + `inspector`: 1-й ESC закрывает interact, 2-й ESC закрывает inspector.
- Ввод в input на interact-панели: ESC не должен приводить к отмене flow.


### AC-4. Pointer UX (клик “в пустоту”)

- MUST: клик по canvas background закрывает все открытые окна и отменяет активный flow (interact).
- MUST: если открыты `interact` + `inspector`, один outside-click закрывает оба.
- NOTE: это намеренно “жёсткое” поведение (hard dismiss), чтобы не оставлять «осиротевшие» окна / активный flow без контекста.

Проверка:
- Открыть interact-панель → клик по canvas закрывает панель.
- Открыть edge-detail или node-card → клик по canvas закрывает их.
- Открыть interact + inspector → клик по canvas закрывает оба.


### AC-5. Геометрия: измерение DOM и reclamp
- MUST: менеджер использует ResizeObserver измерения фактического DOM.
- MUST: на изменение размеров окна или viewport вызывается reclamp.
- MUST: окно всегда clamped внутри `.root` viewport, с минимальным pad 12px.
- MUST: координаты снапаются к 8px сетке:
  - initial placement (до первого измерения): snap всегда,
  - post-measurement: snap применяется **только если** окно пришлось сдвигать clamp'ом обратно в viewport ("snap-on-clamp"); если окно уже in-bounds — позиция сохраняется без джампов.

Проверка:
- Переключение контента (например picking → confirm) не должно выталкивать окно за viewport.
- Resize окна браузера не оставляет окно “снаружи”.


### AC-6. Визуальный контракт WM: geometry-only (frameless) + без «окно в окне»
- MUST: в режиме WM `WindowShell` по умолчанию используется как **geometry-only контейнер** (`frameless=true`):
  - не рисует собственный header,
  - не рисует собственную surface (background/border/shadow/backdrop),
  - не навязывает `width/height` (используется intrinsic sizing контента),
  - не клипает legacy-эффекты внешним `overflow:hidden`.
- MUST: визуальный “frame” (surface/header/кнопки закрытия) остаётся внутри legacy-компонентов.
- MUST: не должно быть дублей: второй surface или второй `×` не появляется.

Проверка:
- В текущем runtime (WM-only) `WindowShell` НЕ содержит `.ws-header` и `button.ws-close`.
- NodeCardOverlay/EdgeDetailPopup/Interact-панели в WM выглядят 1:1 как в legacy и без вложенных поверхностей.


### AC-7. Единый transition для всех окон
- MUST: одна анимация (transform+opacity) применяется ко всем `WindowShell`.
- MUST: replace (например edge-detail → interact-panel) выглядит как замещение.
- MUST: respect `prefers-reduced-motion`.

Проверка:
- Открыть/закрыть разные типы окон: анимация одинаковая.


### AC-8. Реальный UX симптом: консистентные паддинги/размеры без “двойной поверхности”
- MUST: в режиме WindowManager окно не должно иметь “двойной header” или двойной surface-контейнер.
- MUST: отступы контента определяются DS примитивами (например `.ds-panel__body`) и выглядят одинаково.

Проверка:
- В WM-режиме **не появляется дополнительный header** от `WindowShell` (т.е. нет дублирующегося “WS header”).
- Визуально нет двойной рамки/двойного паддинга.

---

## 2) Implementation Map (где это в коде)

Статусы: ✅ соответствует, ⚠️ частично, ❌ расходится.

- AC-1/2/3/4/5:
  - ✅ `simulator-ui/v2/src/composables/windowManager/useWindowManager.ts`
  - ✅ `simulator-ui/v2/src/components/SimulatorAppRoot.vue` (WM layer wiring)

- AC-6/7:
  - ✅ `simulator-ui/v2/src/components/WindowShell.vue` (frameless mode + RO measurement + transition)
  - ✅ `simulator-ui/v2/src/components/SimulatorAppRoot.vue` (WM layer uses `:frameless="true"`)
  - ✅ tests:
    - `simulator-ui/v2/src/components/WindowShell.test.ts`
    - `simulator-ui/v2/src/components/SimulatorAppRoot.interact.test.ts`

Примечания по трассировке требований:
- Layering priority (`interact` > `inspector`): `GROUP_BASE_Z` + `effectiveZ` (визуальный z-index), сортировка `windows`, topmost/ESC по `effectiveZ`.
- Snap strategy C (snap-on-clamp): `reclamp()` применяет `snap8InRange()` только когда `didClamp=true`.

- AC-8 (двойной header/surface):
  - ✅ Исправлено переходом на WM-only runtime: окна управляются только WindowManager, а компоненты окон больше не содержат legacy self-positioning.
  - ✅ В WM-слое окна рендерятся через `WindowShell` (frameless geometry wrapper), без дополнительного “WS header”.

---

## 3) Трассировка критического потока: `tx.updated` (real-mode)

### 3.0 Interact Actions → SSE (`payment-real`)

В real-mode пользовательский жест “Confirm payment” уходит в backend как action-запрос.

- Endpoint:
  - `POST /runs/{run_id}/actions/payment-real`
  - Реализация: `app/api/v1/simulator.py` → `action_payment_real(...)`
- После успешного `PaymentService.create_payment_internal(... commit=True)` backend делает best-effort SSE эмиссию `tx.updated`:
  - строит `edges` (из `res.routes[0].path`, либо fallback `from→to`)
  - вычисляет `edge_patch/node_patch` через `_compute_viz_patches_best_effort(...)`
  - вызывает `SseEventEmitter.emit_tx_updated(...)`

### 3.1 Backend → SSE payload

Ключевой контракт: фронт ожидает edge refs с ключами `from`/`to` (не `from_`).

- Эмиссия события:
  - `app/core/simulator/sse_broadcast.py` → `SseEventEmitter.emit_tx_updated(...)`
  - Pydantic model: `app/schemas/simulator.py` → `SimulatorTxUpdatedEvent` (+ `SimulatorTxUpdatedEventEdge` с alias `from`)
  - Важно: сериализация идёт через `.model_dump(mode="json", by_alias=True)`.

- Patch-поля:
  - `edge_patch` / `node_patch` добавляются как runtime-extension (после Pydantic schema) в `emit_tx_updated`.


### 3.2 Frontend SSE → normalize → apply patches → FX → wakeUp

- SSE loop и обработка событий:
  - `simulator-ui/v2/src/composables/useSimulatorRealMode.ts`:
    - `connectSse(... onMessage ...)`
    - `normalizeSimulatorEvent(parsed)`
    - `isTxUpdatedEvent(evt)` ветка:
      - `realPatchApplier.applyNodePatches(tx.node_patch)`
      - `realPatchApplier.applyEdgePatches(tx.edge_patch)`
      - `runRealTxFx(tx)`
      - `wakeUp?.()` (вывести render loop из deep-idle)
      - `pushTxAmountLabel(...)` (sender/receiver labels с throttle + ttl)

- FX генерация:
  - `runRealTxFx` живёт в `simulator-ui/v2/src/composables/useSimulatorApp.ts` и спавнит sparks/pulses через `simulator-ui/v2/src/render/fxRenderer/*`.

Диагностика:
- Если исчезли labels/FX в real-mode, первое место проверки — наличие `node_patch/edge_patch` и ключей `from/to` в SSE payload.

---

## 4) План проверки (детально, выполнимо)

### 4.1 Manual smoke (10–15 минут)
1) WM включён (флаг/константа проекта):
   - открыть NodeCard → открыть payment → проверить coexistence + 2×ESC.
2) EdgeDetail → Send Payment → убедиться, что EdgeDetail остаётся под панелью, а панель не имеет внутреннего “ESC to close”.
3) EdgeDetail → Change limit → verify replace animation/геометрия.
4) Resize viewport (узко/широко) → окна остаются clamped.

### 4.2 Автотесты (быстро)
- `npm --prefix simulator-ui/v2 run test:unit`
  - регрессии WM/ESC/close-политик/WM-layer wiring.

### 4.3 Доп. диагностика real-mode (при спорных баг-репортах)
- `scripts/diagnose_simulator_sse.py` для метрик качества SSE payload (в т.ч. `tx.updated` patch size).

---

## 5) План рефакторинга (последующие маленькие PR)

Цель: убрать “двойные источники правды” и сделать WM режим единственным для окон в interact UI.

1) Удалить legacy self-positioning из interact-панелей в WM пути
  - Статус: legacy self-positioning выпилен; runtime WM-only.

2) Единый контракт padding/scroll
  - Зафиксировать, что padding даёт `.ds-panel__body`.
  - Скролл/клиппинг:
    - в framed-mode (если он вернётся) — через `WindowShell .ws-body`
    - в frameless WM — внутри legacy компонентов (WM контейнер не должен навязывать overflow)
  - Запретить вложенные “panel surfaces” внутри `WindowShell` (кроме случаев, где это осознанный sub-panel).

3) A11y/Focus (если будет требование на MVP)
  - Добавить критерии: фокус переводится в окно при open, возвращается после close, tab-cycle не уходит в canvas.
  - Реализация может быть минимальной: `focus()` на первое интерактивное поле + возврат на элемент-инициатор.

4) Удалить/замкнуть legacy ESC stack
  - Когда WM включён, `escOverlayStack.ts` должен быть полностью неактивен (и покрыт тестом “не используется”).

---

## 6) Review process (как не пропускать такие регрессии)

Definition of Done для PR, меняющих окна/overlays:
- Прогон `npm --prefix simulator-ui/v2 run test:unit`.
- Скрин-ревью 3 сцен: (1) NodeCard + payment, (2) EdgeDetail → Send Payment, (3) resize viewport.
- Проверка AC-матрицы из раздела 1: хотя бы AC-2/3/6/8.
- Если PR трогает backend события/схемы: validate, что `tx.updated` содержит `from/to` и патчи (по unit/integration тестам backend).

---

## 7) Открытые вопросы / риски

- A11y: фокус-трап и возврат фокуса после закрытия окна пока не формализованы как MUST (можно добавить отдельный AC-блок, если требуется).
- Узкие экраны: в спеке есть идея bottom-sheet для interact на узких viewport (POST-MVP) — сейчас не критерий MVP.
