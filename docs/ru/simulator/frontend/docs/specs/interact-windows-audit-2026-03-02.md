# Комплексный аудит поведения окон с карточками в Interact mode

> **Дата**: 2026-03-02  
> **Тип документа**: Code Review + UX Review  
> **Scope**: оконная система (WindowManager, WindowShell, NodeCard, Interact panels, EdgeDetail)  
> **Ветка**: текущий `main` на момент аудита  
> **Статус**: DRAFT v2 (обновлено по результатам ревью)

Связанные спецификации:
- План удаления legacy runtime (WM-only) + референс через скриншоты: [legacy-removal-wm-only-plan-2026-03-03.md](legacy-removal-wm-only-plan-2026-03-03.md)
- Референс верстки legacy окон (HTML snapshots): [legacy-windows-reference/README.md](legacy-windows-reference/README.md)

Примечание по ссылкам на код:
- Ссылки на строки в `.vue/.ts` файлах — ориентировочные (они быстро дрейфуют при правках).
- Этот документ старается ссылаться на **файлы/символы/сценарии**, а не на стабильность номеров строк.

---

## Содержание

- [1. Архитектурный обзор](#1-архитектурный-обзор)
- [2. Пайплайны ключевых сценариев](#2-пайплайны-ключевых-сценариев)
  - [2.1 Открытие NodeCard по клику на ноду](#21-открытие-nodecard-по-клику-на-ноду)
  - [2.2 Открытие вспомогательной панели из NodeCard](#22-открытие-вспомогательной-панели-из-nodecard)
  - [2.3 Открытие вспомогательной панели из ActionBar](#23-открытие-вспомогательной-панели-из-actionbar)
  - [2.4 Переключение между карточками](#24-переключение-между-карточками-клик-на-другую-ноду)
  - [2.5 Закрытие вспомогательной панели](#25-закрытие-вспомогательной-панели)
  - [2.6 Закрытие первичной карточки](#26-закрытие-первичной-карточки-nodecard)
  - [2.7 ESC step-back в interact-panel](#27-esc-step-back-в-interact-panel)
  - [2.8 Outside-click (canvas click)](#28-outside-click-canvas-click)
  - [2.9 Автообновление данных в открытом окне](#29-автообновление-данных-в-открытом-окне)
  - [2.10 Смена размеров/контента после открытия](#210-смена-размеровконтента-после-открытия)
  - [2.11 Восстановление фокуса после закрытия](#211-восстановление-фокуса-после-закрытия)
- [3. Найденные проблемы](#3-найденные-проблемы)
  - [3.1 UX-аномалии](#31-ux-аномалии)
  - [3.2 Архитектурные проблемы](#32-архитектурные-проблемы)
  - [3.3 Производительность](#33-производительность)
  - [3.4 Race conditions](#34-race-conditions)
- [4. Анализ ресайзов](#4-анализ-ресайзов)
- [5. План рефакторинга](#5-план-рефакторинга)
- [6. Тесты и наблюдаемость](#6-тесты-и-наблюдаемость)
- [7. Критерии приемки рефакторинга](#7-критерии-приемки-рефакторинга)
- [8. Decision Log](#8-decision-log)
  - [8.1 Canvas empty click policy](#81-canvas-empty-click-policy)
  - [8.2 wm.open семантика upsert](#82-wmopen-семантика-upsert)
  - [8.3 ESC debounce vs closing state](#83-esc-debounce-vs-closing-state)
- [9. Типология perceived jumps](#9-типология-perceived-jumps)

---

## 1. Архитектурный обзор

### 1.1 Типы окон и группы

Система управления окнами определена в [`types.ts`](simulator-ui/v2/src/composables/windowManager/types.ts):

```
WindowType  = 'interact-panel' | 'node-card' | 'edge-detail'
WindowGroup = 'interact'       | 'inspector'
```

| Тип окна        | Группа     | singleton | escBehavior      | closeOnOutsideClick |
|-----------------|-----------|-----------|------------------|---------------------|
| interact-panel  | interact  | reuse     | back-then-close  | false               |
| node-card       | inspector | reuse     | close            | true                |
| edge-detail     | inspector | reuse     | close            | true                |

**Инварианты групп:**
- `interact`: максимум 1 окно (Payment XOR Trustline XOR Clearing)
- `inspector`: максимум 1 окно (NodeCard XOR EdgeDetail)
- Группы **сосуществуют**: interact-panel + NodeCard/EdgeDetail одновременно допустимо

### 1.2 Карта модулей

```
SimulatorAppRoot.vue (1600+ строк)
├── useWindowManager()          → wm: WindowManagerApi
│   ├── windowsMap: reactive(Map)
│   ├── open() / close() / handleEsc()
│   ├── reclamp() / updateMeasuredSize()
│   └── getPolicy() / getConstraints()
├── WindowShell.vue             → frameless geometry wrapper
│   ├── ResizeObserver → emit('measured')
│   └── CSS transitions (ws-enter/leave)
├── useSimulatorApp()            → facade: canvas click/dblclick, selection, interact mode
│   ├── __selectNodeFromCanvasStep0() (Step 0 policy)
│   └── openNodeCardFromCanvasDblClick() → opts.uiOpenOrUpdateNodeCard()
├── useCanvasInteractions()       → click/dblclick + suppression for pan/drag gestures
├── useInteractFSM()            → 11 фаз
├── useInteractMode()           → epoch-based async runner
├── useInteractDataCache()      → TTL-based cache
├── useInteractPanelPosition()  → panelAnchor reset
└── interactWindowOfPhase()     → phase → window type mapping
```

### 1.3 Два рендер-пути (Legacy vs WM)

Исторически (на момент этого аудита) существовали два рендер-пути (Legacy overlays vs WindowManager), и был opt-out через query string. В текущем коде legacy opt-out удалён, runtime — WM-only.

```
┌─ Legacy path (удалён; исторический контекст) ───────┐
│  <Transition name="panel-slide">                     │
│    → absolute positioning через legacy helpers       │
│    → legacy ESC/outside-click stack                  │
└──────────────────────────────────────────────────────┘

┌─ WM path (текущий runtime) ─────────────────────────┐
│  <TransitionGroup name="ws" class="wm-layer">       │
│    v-for win in wm.windows.value                     │
│    → <WindowShell :frameless="true">                 │
│    → wm.handleEsc() для ESC                          │
│    → ResizeObserver → updateMeasuredSize → reclamp   │
└──────────────────────────────────────────────────────┘
```

### 1.4 Источники правды (Source of Truth)

В текущем runtime legacy-путь удалён (WM-only). Поэтому ниже — только актуальные источники правды.

| Данные                    | Источник правды (WM-only runtime) |
|--------------------------|-----------------------------------|
| Позиция окна             | `wm` window `rect`                |
| Размер окна              | `wm` window `measured`            |
| Открыто ли окно          | `wm` windows list/map (по `type` / `id`) |
| Активное окно            | `wm.activeId`                     |
| Z-порядок                | `win.z` (focusCounter)            |
| Фаза Interact            | `useInteractFSM.phase`            |

---

## 2. Пайплайны ключевых сценариев

### 2.1 Открытие NodeCard по клику на ноду

**Точка входа:** двойной клик на canvas-ноде
**Источник события:** `useCanvasInteractions.onCanvasDblClick()` → hook `useSimulatorApp` (dblclick перехватывается и открывает NodeCard).

> UPDATE (2026-03-03): нормативное поведение в WM runtime:
> - single click по узлу в picking-фазах Interact выбирает текущий шаг (в т.ч. `TO` для payment);
> - double click по узлу выбирает узел и открывает NodeCard (через WM);
> - никаких legacy open/close boolean refs в runtime не используется.

```
User: dblclick на ноду
  │
  ▼
onCanvasDblClick(ev)
  │ pickNodeAt(ev.clientX, ev.clientY) → hit.id
  │ onNodeDblClick(hit) → handled=true
  │
  ▼
openNodeCardFromCanvasDblClick(nodeId)
  │ selectNode(nodeId)
  │ opts.uiOpenOrUpdateNodeCard({ nodeId, anchor: selectedNodeScreenCenter })
  │   → wm.open({ type:'node-card', anchor, data:{ nodeId } })
  │     singleton=reuse (upsert)
  │     closeGroupExcept('inspector', id) (edge-detail XOR node-card)
  │     focus(id) + reclamp()

Side effects:
  - closeGroupExcept закрывает предыдущий inspector (EdgeDetail если был)
  - focusCounter++ → z-order поднимается
  - Первый кадр: estimated size (360×260), не реальный
  - Второй+ кадр: ResizeObserver → measured → reclamp (возможен скачок)

Владельцы состояния:
  - selectedNodeId: useSimulatorApp
  - windowsMap / activeId / z-order: useWindowManager
  - last opened inspector ids (best-effort): SimulatorAppRoot

Потенциальные гонки:
  - Rapid dblclick по разным нодам → серия синхронных `wm.open(singleton=reuse)` (последний клик побеждает)
```

### 2.2 Открытие вспомогательной панели из NodeCard

**Точка входа:** кнопка Send Payment / New Trustline / Edit Trustline в NodeCardOverlay
**Источник события:** callbacks `onInteractSendPayment` / `onInteractNewTrustline` / `onInteractEditTrustline`

```
User: клик "Send Payment" в NodeCard
  │
  ▼
onInteractSendPayment(fromPid)
  │ interact.mode.beginPayment(fromPid)
  │   FSM: idle → picking-payment-from (если FROM не указан)
  │        idle → picking-payment-to  (если FROM = fromPid, prefilled)
  │   state.initiatedWithPrefilledFrom = true
  │
  ▼
watcher [interactPhase, ...] fires             [SimulatorAppRoot.vue:465]
  │ phase = 'picking-payment-to'
  │ interactWindowOfPhase(phase, isFullEditor)  [interactWindowOfPhase.ts:20]
  │   → {type: 'interact-panel', panel: 'payment'}
  │
  │ anchor = wmInteractAnchor.value
  │   ← wmPanelOpenAnchor || panelAnchor
  │   (panelAnchor устанавливается в openFrom('node-card', snapshot))
  │
  │ wm.open({
  │   type: 'interact-panel',
  │   anchor,
  │   data: makeInteractPanelWindowData('payment', phase)
  │ })
  │   getPolicy() → interact, reuse, back-then-close  [:75]
  │   getConstraints() → 320/220/560/420               [:145]
  │   closeGroup('interact')  ← закрывает предыдущий interact если есть
  │   estimateSize → 560×420 (preferred)
  │   cascadeShiftAvoidOverlaps (avoid NodeCard overlap)
  │   focus + reclamp
  │
  ▼
WindowShell renders interact-panel content
  │ ManualPaymentPanel v-if panel='payment'
  │ Загрузка participants → panel grows → ResizeObserver
  │   emit('measured', {width, height})
  │   → wm.updateMeasuredSize(id, size)   [:209]
  │   → wm.reclamp(id)                    [:220]
  │
  │ Возможный скачок: estimated 560×420 → measured ~560×280
  │ reclamp post-measurement: only clamp if oob, NO re-snap [:258-267]

Side effects:
  - NodeCard остаётся открытым (inspector group не затронута)
  - initiatedWithPrefilledFrom = true → ESC из picking-to закроет окно
  - panelAnchor обновляется → может вызвать repositioning

Владельцы состояния:
  - interactPhase: useInteractFSM
  - wmInteractAnchor: SimulatorAppRoot (computed)
  - windowsMap: useWindowManager
  - participants/trustlines: useInteractDataCache
```

### 2.3 Открытие вспомогательной панели из ActionBar

**Точка входа:** ActionBar кнопки (Send Payment / Trustline / Clearing)
**Контраст с NodeCard:** `initiatedWithPrefilledFrom = false`

```
User: клик "Send Payment" в ActionBar
  │
  ▼
interact.mode.beginPayment(null)
  │ FSM: idle → picking-payment-from
  │ state.initiatedWithPrefilledFrom = false
  │ panelAnchor → сбрасывается или устанавливается из ActionBar позиции
  │
  ▼
watcher fires                                   [SimulatorAppRoot.vue:465]
  │ phase = 'picking-payment-from'
  │ interactWindowOfPhase → {type: 'interact-panel', panel: 'payment'}
  │ wm.open({type: 'interact-panel', ...})
  │
  │ anchor = null или ActionBar anchor
  │   → placement = 'docked-right' (если anchor=null)
  │   → rect.left = viewport.width - width - 12
  │   → rect.top = 110
  │
  │ ИЛИ если anchor есть:
  │   → placement = 'anchored'
  │   → rect = anchor + offset(16,16)
  │
  ▼
Panel renders, user selects FROM participant
  │ FSM: picking-payment-from → picking-payment-to
  │ watcher fires: same window type → singleton reuse
  │   wm.open() → обновляет data/constraints
  │   anchorChanged? → repositioning if yes
  │   !measured → refresh estimate size
  │
  ▼
User selects TO participant
  │ FSM: picking-payment-to → confirm-payment
  │ watcher fires: same window type → singleton reuse
  │   data updated with new phase

Отличие от NodeCard-initiated flow:
  - initiatedWithPrefilledFrom = false
  - ESC в picking-payment-to НЕ закрывает, а делает step-back к picking-from
  - ESC в picking-payment-from закрывает (нет предыдущего шага)
  - Нет NodeCard в inspector group
```

### 2.4 Переключение между карточками (клик на другую ноду)

**Точка входа:** dblclick на другую ноду при открытой NodeCard

```
User: dblclick на ноду B (при открытой NodeCard для ноды A)
  │
  ▼
onCanvasDblClick(ev)
  │ pickNodeAt → hit.id = 'B'
  │ onNodeDblClick(hit) → handled=true
  ▼
openNodeCardFromCanvasDblClick('B')
  │ selectNode('B')
  │ uiOpenOrUpdateNodeCard({ nodeId:'B', anchor })
  │   wm.open({type:'node-card', data:{nodeId:'B'}, anchor})
  │     singleton=reuse → обновляет data
  │     measured уже есть → по умолчанию позиция сохраняется (anchor используется как hint на первом open)
  │     focus + reclamp

Потенциальные проблемы:
  P2: Rapid clicks (A→B→C→D) → серия `wm.open(singleton=reuse)`
  P3: Если Interact flow активен — selectNode направляет в FSM,
      а не в NodeCard → узел B используется как input для flow
```

### 2.5 Закрытие вспомогательной панели

**Способы закрытия:** кнопка ×, Cancel, ESC, programmatic

```
=== Закрытие по × / Cancel ===

User: клик × или Cancel в interact-panel / edge-detail
  │
  ▼
@close event из компонента
  │
  ├─── interact-panel ───────────────────────────┐
  │  wm.close(win.id, 'action')                  │ [SimulatorAppRoot.vue:1346]
  │    policy.onClose('action')                   │ [useWindowManager.ts:96]
  │      d.onClose() → interact.mode.cancel()     │ [SimulatorAppRoot.vue:431]
  │    windowsMap.delete(id)                       │ [useWindowManager.ts:284]
  │    pickNextActiveId → активируем следующее     │ [:294]
  │                                               │
  │  FSM: current phase → idle                    │
  │  watcher fires: phase='idle'                  │
  │    → wm.closeGroup('interact', 'programmatic')│ [SimulatorAppRoot.vue:495]
  │    → НО окно уже удалено → no-op              │
  └───────────────────────────────────────────────┘
  │
  ├─── edge-detail ──────────────────────────────┐
  │  uiCloseEdgeDetailWindow(win.id, 'action')   │ [SimulatorAppRoot.vue:159]
  │    wmEdgeDetailSuppressed = true              │ [:161]
  │    wmResetEdgeDetailKeepAlive()               │ [:162]
  │    wm.close(winId, reason)                    │ [:163]
  │    wmEdgeDetailId = null                      │ [:164]
  │                                               │
  │  ВАЖНО: flow НЕ отменяется                   │
  │  (suppressed скрывает окно, но FSM живёт)    │
  └──────────────────────────────────────────────┘

=== Закрытие по ESC ===

(См. раздел 2.7)

=== Programmatic close ===

wm.closeGroup('interact', 'programmatic')
  │ policy.onClose('programmatic') → НЕ вызывает d.onClose
  │ (reason='programmatic' НЕ является 'esc'|'action')
  │ [useWindowManager.ts:96-101]
  │
  │ ВНИМАНИЕ: interact.mode.cancel() НЕ вызывается!
  │ Вызывающий код должен сам отменить flow.
```

### 2.6 Закрытие первичной карточки (NodeCard)

```
=== Закрытие по × в NodeCard ===

User: клик × в NodeCardOverlay
  │
  ▼
@close emit                                    [SimulatorAppRoot.vue:1391]
  │ wm.close(win.id, 'action')
  │
  │ windowsMap.delete(id)
  │ (WM state — source of truth; локальные `...Id` refs в root — best-effort)
  │
  │ pickNextActiveId → если interact-panel есть → активируем его

=== Закрытие по ESC ===

onGlobalKeydown                                [SimulatorAppRoot.vue:712]
  │ wm.handleEsc(ev, {isFormLikeTarget, dispatchWindowEsc})
  │   → topmost window = NodeCard (если наверху)
  │   → dispatchWindowEsc (CustomEvent 'geo:interact-esc')
  │   → не consumed → policy.escBehavior = 'close'
  │   → close(id, 'esc')
  │     policy.onClose('esc')
  │
  │ windowsMap.delete(id)

Side effects:
  - Если interact-panel был открыт — он становится active
  - Если interact flow не idle — он продолжает работать без NodeCard
  - Фокус не восстанавливается на canvas формально (нет focus-return)
```

### 2.7 ESC step-back в interact-panel

**Ключевой механизм:** `escBehavior: 'back-then-close'` + `policy.onEsc()` в `useWindowManager.ts`

```
User: нажимает ESC при открытом interact-panel
  │
  ▼
onGlobalKeydown(ev)                             [SimulatorAppRoot.vue:712]
  │ ev.key === 'Escape'
  │ runtime: WM-only
  │
  ▼
wm.handleEsc(ev, opts)
  │ isFormLikeTarget(ev.target)?
  │   → true: return false (ESC не потреблён)
  │   → false: продолжаем
  │
  │ Найти topmost окно (max z):
  │   for (win of windowsMap) if win.z > top.z → top = win
  │
  │ dispatchWindowEsc()
  │   → CustomEvent 'geo:interact-esc'
  │   → если dropdown открыт → preventDefault() → return false
  │   → если нет → return true (not canceled)
  │
  │ policy.escBehavior?
  │   'ignore'           → return false
  │   'close'            → close(id, 'esc'); return true
  │   'back-then-close'  → STEP-BACK LOGIC:
  │
  ▼
policy.onEsc()
  │ → d.onBack()                                [SimulatorAppRoot.vue:396]
  │
  │ Логика onBack по фазам:
  │
  │ ┌─ confirm-payment ──────────────────────────┐
  │ │ interact.mode.setPaymentToPid(null)         │
  │ │ FSM → picking-payment-to                    │
  │ │ return true → 'consumed'                    │
  │ └────────────────────────────────────────────┘
  │
  │ ┌─ picking-payment-to ──────────────────────┐
  │ │ if initiatedWithPrefilledFrom:             │
  │ │   return false → 'pass' → close()         │ ← 1 ESC closes
  │ │ else:                                      │
  │ │   setPaymentFromPid(null)                  │
  │ │   FSM → picking-payment-from               │
  │ │   return true → 'consumed'                 │ ← step-back
  │ └────────────────────────────────────────────┘
  │
  │ ┌─ editing-trustline / confirm-trustline ───┐
  │ │ setTrustlineToPid(null)                    │
  │ │ FSM → picking-trustline-to                 │
  │ │ return true → 'consumed'                   │
  │ └────────────────────────────────────────────┘
  │
  │ ┌─ picking-trustline-to ────────────────────┐
  │ │ if initiatedWithPrefilledFrom:             │
  │ │   return false → 'pass' → close()         │
  │ │ else:                                      │
  │ │   setTrustlineFromPid(null)                │
  │ │   return true → 'consumed'                 │
  │ └────────────────────────────────────────────┘
  │
  │ ┌─ picking-payment-from ────────────────────┐
  │ │ return false → 'pass' → close(id, 'esc')  │
  │ │ policy.onClose('esc')                      │
  │ │   d.onClose() → interact.mode.cancel()     │
  │ │ FSM → idle                                 │
  │ └────────────────────────────────────────────┘
  │
  │ ┌─ clearing phases ─────────────────────────┐
  │ │ return false → 'pass' → close(id, 'esc')  │
  │ │ → interact.mode.cancel()                   │
  │ │ No step-back for clearing                  │
  │ └────────────────────────────────────────────┘

watcher fires on phase change:
  │ new phase → interactWindowOfPhase() → same type
  │ → wm.open() singleton=reuse → updates data
  │ → NO new window created, position preserved

Цепочка ESC для Payment из ActionBar (3 шага):
  ESC #1: confirm-payment → picking-payment-to (step-back)
  ESC #2: picking-to → picking-from (step-back)
  ESC #3: picking-from → close → idle (close)

Цепочка ESC для Payment из NodeCard (1 шаг):
  ESC #1: confirm-payment → picking-payment-to (step-back)
  ESC #2: picking-to → close (initiatedWithPrefilledFrom=true)

NOTE: между шагами watcher обновляет data через wm.open(singleton=reuse),
что может вызвать repositioning если anchor изменился.
```

### 2.8 Outside-click (canvas click)

**Реализация (актуально):** hard dismiss (закрыть inspector + cancel interact)

```
User: клик на пустое место canvas
  │
  ▼
onCanvasClick(ev)                               [useCanvasInteractions.ts:61]
  │ pickNodeAt → null (нет ноды)
  │ setSelectedNodeId(null)
  │
  ├─── WM mode ─────────────────────────────────┐
  │                                              │
  │ __selectNodeFromCanvasStep0({id: null, ...}) │ [useSimulatorApp.ts:132]
  │   closeTopmostOverlayOnOutsideClick()        │  (x2)
  │     → uiCloseTopmostInspectorWindow()        │ [SimulatorAppRoot.vue:167]
  │                                              │
  │ uiCloseTopmostInspectorWindow():             │
  │   top = wm.getTopmostInGroup('inspector')    │ [:172]
  │   wmResetEdgeDetailKeepAlive()               │ [:182]
  │                                              │
  │   wm.close(top.id, 'programmatic')           │
  │   (второй вызов) → закрывает следующую карточку (если была)
  │                                              │
  │   РЕЗУЛЬТАТ: закрыты обе inspector-карточки (edge-detail + node-card)
  │   + Interact flow отменён (phase → idle) → interact-panel закрыт
  └──────────────────────────────────────────────┘

ВАЖНО:
  - Empty click трактуется как UI-close inspector карточек.
  - Одновременно empty click отменяет interact flow (hard dismiss).
  - Закрытие делается через 2 последовательных UI-close (topmost inspector → следующий inspector).
```

### 2.9 Автообновление данных в открытом окне

```
SSE snapshot update приходит
  │
  ▼
useSimulatorApp processes SSE event
  │ state.snapshot обновляется
  │
  ├─── NodeCard ────────────────────────────────────┐
  │ selectedNode = getNodeById(selectedNodeId)       │
  │ → computed пересчитывается                       │
  │ → NodeCardOverlay props обновляются              │
  │ → Vue reactivity → re-render                     │
  │                                                  │
  │ WM: окно не «привязано» к ноде после открытия.   │
  │ Авто-перемещение по layout change не происходит  │
  │ (by design: user может перетаскивать окно).      │
  └──────────────────────────────────────────────────┘
  │
  ├─── Interact panel ──────────────────────────────┐
  │ useInteractDataCache:                            │
  │   TTL checks → stale? → refetch                 │
  │   participants TTL=30s                           │
  │   trustlines TTL=15s                             │
  │   payment targets TTL=10s                        │
  │                                                  │
  │ panel re-renders with new data                   │
  │ → ResizeObserver fires if size changed           │
  │ → wm.updateMeasuredSize + reclamp               │
  │                                                  │
  │ Возможный скачок при смене размера контента      │
  └──────────────────────────────────────────────────┘
  │
  ├─── EdgeDetail ──────────────────────────────────┐
  │ interactSelectedLink обновляется                 │
  │ wmEdgeDetailEffectiveLink → live data            │
  │ ИЛИ wmEdgeDetailKeepAlive → frozen data          │
  │ → component props update → re-render             │
  └──────────────────────────────────────────────────┘
```

### 2.10 Смена размеров/контента после открытия

```
=== First frame (estimated size) ===

wm.open()
  │ estimateSizeFromConstraints(constraints)    [geometry.ts:9]
  │   width = preferredWidth ?? minWidth
  │   height = preferredHeight ?? minHeight
  │
  │ interact-panel: 560×420 (payment/clearing), 380×420 (trustline)
  │ node-card: 360×260
  │ edge-detail: 420×320
  │
  │ ВАЖНО (WM frameless): WindowShell НЕ выставляет width/height в inline style.
  │ Визуальный размер в первом кадре = intrinsic content sizing,
  │ но с минимальными ограничениями (min-width/min-height) из constraints,
  │ чтобы не показывать «tiny loading stub».
  │ При этом WM использует estimated size для:
  │   - расчёта стартовой `rect` (геометрии),
  │   - collision avoidance (cascadeShift),
  │   - первого reclamp (clamp по viewport).
  │ Если estimate сильно расходится с реальным измерением, возможен
  │ видимый «скачок позиции» на первом measured→reclamp.

=== ResizeObserver callback ===

WindowShell.onMounted → ResizeObserver          [WindowShell.vue:81]
  │ entry.borderBoxSize → {inlineSize, blockSize}
  │ emit('measured', {width, height})
  │
  ▼
SimulatorAppRoot @measured handler              [SimulatorAppRoot.vue:1348]
  │ wm.updateMeasuredSize(id, size)             [useWindowManager.ts:209]
  │   win.measured = {width, height}
  │ wm.reclamp(id)                              [:220]
  │   measured exists → post-measurement reclamp
  │   → only clamp if out-of-bounds
  │   → NO re-snap8 (avoid position jump)       [:258]
  │   → win.rect.width = measured.width
  │   → win.rect.height = measured.height

=== Content change (loading → loaded) ===

Panel starts: загрузка participants
  │ minimal content → small measured size (~100-200px height)
  │
  │ participants loaded → list renders
  │ → panel grows to ~400-500px height
  │ → ResizeObserver fires again
  │ → updateMeasuredSize + reclamp
  │
  │ ВИДИМЫЙ СКАЧОК: panel "растёт" после подгрузки
  │ reclamp может сдвинуть позицию если panel вышел за viewport

=== Hardcoded vs dynamic sizes ===

WM getConstraints:                              [useWindowManager.ts:143]
  │ node-card: preferredWidth=360, preferredHeight=260
  │ → estimateSize = 360×260
  │ → реальный размер может быть 340×350+ (с interact кнопками)
  │ → первый кадр показывает "обрезанный" или "пустой" контент
```

### 2.11 Восстановление фокуса после закрытия

```
User: закрывает окно (×, ESC, outside-click)
  │
  ▼
wm.close(id, reason)                           [useWindowManager.ts:281]
  │ windowsMap.delete(id)
  │ wasActive? → pickNextActiveId()             [:293]
  │   → setActive(nextId)
  │   → обновляет win.active для всех окон
  │
  ▼
TransitionGroup: ws-leave-active → ws-leave-to
  │ CSS: opacity 0, translateY(8px), 180ms
  │
  ▼
DOM element removed after transition
  │
  ▼
ФОКУС НЕ ВОССТАНАВЛИВАЕТСЯ!
  │ Нет focus-return mechanism
  │ Нет aria-activedescendant / focus-trap
  │ document.activeElement → body или last focused
  │
  │ ПОСЛЕДСТВИЯ:
  │ - Keyboard users потеряют фокус
  │ - Screen readers не объявят контекст
  │ - Tab navigation может начаться с неожиданного элемента

Side effects pickNextActiveId:
  - Перебирает все оставшиеся окна, выбирает max z
  - Если окон не осталось → activeId = null
  - Если осталось interact-panel → оно становится active
  - НО "active" в WM это лишь z-order/state flag,
    это НЕ означает DOM focus
```

---

## 3. Найденные проблемы

### 3.1 UX-аномалии

#### UX-1: Видимый ресайз/скачок при открытии окна

**Описание:** При открытии interact-panel пользователь видит:
1. Первый кадр — estimated size (560×420), часто с пустым loading stub
2. Контент загружается → panel растёт/сужается
3. ResizeObserver → reclamp → возможен сдвиг позиции

**Первопричина:** [`estimateSizeFromConstraints()`](simulator-ui/v2/src/composables/windowManager/geometry.ts:9) возвращает `preferredWidth/Height`, которые НЕ совпадают с реальным размером контента в конкретной фазе.

Для interact-panel payment: preferred=560×420, но:
- picking-from: ~560×280 (dropdown + header only)
- confirm-payment: ~560×420 (полная форма)
- loading state: ~560×100 (spinner only)

**Последствия:**
- Визуальное "мерцание" при открытии
- В frameless mode минимальная ширина применяется, но высота определяется контентом → скачок высоты

**Способ воспроизведения:**
1. Открыть NodeCard
2. Нажать "Send Payment"
3. Наблюдать первый кадр loading state → loaded state переход

**Критичность:** P2 (заметно, но не блокирует)

**Вариант исправления:**
- Offscreen pre-measure: рендерить panel в `visibility:hidden` → измерить → показать
- Phase-dependent preferred sizes: разные preferredHeight для loading/picking/confirm
- Skeleton placeholder с фиксированной высотой

---

#### UX-2 (Done): Потеря фокуса при переключении/закрытии окон

**Описание:** При закрытии окна фокус не восстанавливается на предыдущий элемент.

**Первопричина:** [`pickNextActiveId()`](simulator-ui/v2/src/composables/windowManager/useWindowManager.ts:45) обновляет только `win.active` flag, но не вызывает `element.focus()`. Нет focus-trap и нет focus-return стека.

**Последствия:**
- Keyboard-only users теряют контекст
- Tab navigation начинается с `<body>` или произвольного элемента
- Screen readers не анонсируют переход

**Способ воспроизведения:**
1. Tab-навигация к NodeCard
2. Нажать ESC
3. Проверить `document.activeElement`

**Критичность:** P2 (accessibility, не UX-блокирующий для mouse users)

**Вариант исправления:**
- Focus-return stack: при открытии окна запоминать `document.activeElement`
- При закрытии → `previousElement.focus()`
- Focus-trap внутри framed windows (в frameless — optional) — **TODO, не реализовано** (см. [Известные TODO (из кода)](#известные-todo-из-кода))

**Статус:** ✅ Done (focus-return + базовая семантика dialog)

**Граница scope:** focus-trap **не реализован** и остаётся отдельным TODO (см. [Известные TODO (из кода)](#известные-todo-из-кода)).

**Результат (implementation notes):**
- В `WM` добавлен **focus-return stack (LIFO)**: при открытии окна запоминается элемент, имевший фокус, а при закрытии — фокус возвращается на последний валидный элемент из стека.
- `WindowShell` формализован как диалог: добавлены `role="dialog"` и `aria-label`.
- Focus-trap не добавлялся: текущая правка покрывает только focus-return и базовую семантику диалога (см. [Известные TODO (из кода)](#известные-todo-из-кода)).

**Где в коде:**
- Focus-return stack: [`useWindowManager.ts`](simulator-ui/v2/src/composables/windowManager/useWindowManager.ts:1)
- A11y атрибуты оболочки: [`WindowShell.vue`](simulator-ui/v2/src/components/WindowShell.vue:1)

**Тесты (DoD):**
- Unit tests для focus-return поведения: [`useWindowManager.test.ts`](simulator-ui/v2/src/composables/windowManager/useWindowManager.test.ts:1)
- Unit tests для a11y атрибутов shell: [`WindowShell.test.ts`](simulator-ui/v2/src/components/WindowShell.test.ts:1)

---

#### UX-3: Двойное закрытие при быстрых ESC (closing state вместо debounce)

**Описание:** Если пользователь нажимает ESC дважды быстро (< 180ms — время анимации), первый ESC закрывает topmost окно, но второй может "проскочить" к следующему окну.

**Первопричина:** [`handleEsc()`](simulator-ui/v2/src/composables/windowManager/useWindowManager.ts:444) находит topmost окно синхронно. После `close()` окно сразу удаляется из `windowsMap`, но DOM-элемент ещё в leave-transition. Второй ESC находит следующее окно и закрывает его.

**Последствия:**
- interact-panel + NodeCard: двойной ESC может закрыть оба
- Пользователь ожидал step-back, но получил полное закрытие

**Способ воспроизведения:**
1. Открыть NodeCard + interact-panel (confirm-payment)
2. Быстро нажать ESC 2-3 раза
3. Оба окна закрываются (panel + NodeCard)

**Критичность:** P2

**Вариант исправления (пересмотрен по итогам ревью):**

> ⚠️ Первоначальный вариант "Debounce ESC 180ms" **отклонён** — быстрый повтор ESC часто
> является осознанным "закрыть всё" и debouce ломает этот UX-паттерн.

**Рекомендуемый подход — closing state / deferred delete:**
- При `close()` окно переходит в состояние `closing` (вместо мгновенного удаления из `windowsMap`)
- Окно в `closing` state: остаётся в `windowsMap` для `handleEsc()`, но видимо в leave-transition
- Второй ESC "понимает" что topmost окно уже закрывается и пропускает его
- После завершения transition → окончательное удаление из `windowsMap`
- Это позволяет быстрому "ESC ESC ESC" корректно проходить по стеку (каждое нажатие закрывает следующее)

См. также: [8.3 ESC debounce vs closing state](#83-esc-debounce-vs-closing-state)

---

#### UX-4 (Fixed): Canvas empty click = hard dismiss (cancel flow)

**Норма:** Пустой клик по canvas закрывает **edge-detail + node-card** (если они открыты), очищает selection и **отменяет interact flow**.

**Критичность:** P1 → закрыто (зафиксировано как hard dismiss).

---

#### UX-5 (Done): Повторное открытие той же карточки

**Описание:** Двойной клик на ту же ноду при открытой карточке — неопределённое поведение.

**Первопричина:** dblclick по ноде всегда вызывает `openNodeCardFromCanvasDblClick(nodeId)`.
Если карточка уже открыта для того же `nodeId`, это приводит к повторному `wm.open(singleton=reuse)`.

**Последствия:**
- Возможный неожиданный `focus()` (z-order jump) без видимой причины
- Лишняя работа (повторный upsert data/anchor/constraints)

**Способ воспроизведения:**
1. Двойной клик на ноду A → карточка открывается
2. Двойной клик на ту же ноду A
3. Наблюдать мерцание

**Критичность:** P3

**Вариант исправления:**
- Early return в `openNodeCardFromCanvasDblClick()`: если topmost node-card уже показывает этот `nodeId`
  и нет причин обновлять anchor/data — не вызывать `wm.open()`.

**Статус:** ✅ Done

**Результат:**
- Повторный dblclick на ту же ноду при открытой topmost NodeCard больше не вызывает [`wm.open()`](simulator-ui/v2/src/composables/windowManager/useWindowManager.ts:1) (early return если `nodeId` совпадает).

**Где в коде:**
- Guard/early return для повторного открытия той же ноды: [`SimulatorAppRoot.vue`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:1)

**Тесты (DoD):**
- Integration test на повторный dblclick по той же ноде ([`wm.open()`](simulator-ui/v2/src/composables/windowManager/useWindowManager.ts:1) не вызывается): [`SimulatorAppRoot.interact.test.ts`](simulator-ui/v2/src/components/SimulatorAppRoot.interact.test.ts:1)

---

#### UX-6 (Done): Быстрые последовательные клики по разным нодам

**Описание:** При быстрых кликах по нодам A→B→C→D watchers coalesce, но `uiOpenOrUpdateNodeCard` вызывается синхронно каждый раз.

**Первопричина:** `wm.open()` с `singleton=reuse` обновляет data и anchor для существующего окна. Каждый вызов меняет anchor → repositioning. При rapid clicks:
1. click A → open(nodeA, anchorA) → window at posA
2. click B → watcher fires → open(nodeB, anchorB) → window moves to posB
3. click C → watcher fires → open(nodeC, anchorC) → window moves to posC

Каждый repositioning может вызвать reclamp и cascadeShift.

**Последствия:**
- Визуальное "прыгание" окна по экрану при rapid clicks
- Избыточные вычисления (4-5 open() вызовов за 500ms)

**Способ воспроизведения:**
1. Быстро кликнуть по 4-5 разным нодам
2. Наблюдать "телепортацию" карточки

**Критичность:** P3

**Вариант исправления:**
- Debounce на уровне watcher (50-100ms)
- Или: throttle `wm.open()` для singleton=reuse windows
- Или: animation-skip при anchor change (instant repositioning)

**Статус:** ✅ Done

**Результат:**
- Добавлен trailing debounce **90ms** для [`wm.open()`](simulator-ui/v2/src/composables/windowManager/useWindowManager.ts:1) в режиме `singleton='reuse'` для `type: 'node-card'`: при rapid clicks A→B→C… применяется только последний payload (last intent wins), без серии промежуточных reposition/jumps.
- Debounce применяется **только** к `node-card` + `reuse` и не затрагивает другие типы окон/режимы.
- Debounce **не влияет** на watcher-driven «anchor follow» из UX-9: вызовы [`wm.open()`](simulator-ui/v2/src/composables/windowManager/useWindowManager.ts:1) с `focus: 'never'` не дебаунсятся.

**Где в коде:**
- Реализация trailing debounce (90ms) для `node-card` reuse: [`useWindowManager.ts`](simulator-ui/v2/src/composables/windowManager/useWindowManager.ts:1)

**Тесты (DoD):**
- Unit test: trailing debounce 90ms + применяется только последний payload: [`useWindowManager.test.ts`](simulator-ui/v2/src/composables/windowManager/useWindowManager.test.ts:1)

---

#### UX-7: Неожиданные z-order «скачки» на реактивных апдейтах

**Описание:** При приходе данных (SSE / TTL refetch / phase re-render) открытое окно может
неожиданно «всплывать» поверх остальных, хотя пользователь не кликал по нему.

**Первопричина:** `wm.open()` всегда вызывает `focus(id)` даже при `singleton='reuse'` и
обновлении существующего окна ([`useWindowManager.ts:391-399`](simulator-ui/v2/src/composables/windowManager/useWindowManager.ts:391),
[`useWindowManager.ts:439-444`](simulator-ui/v2/src/composables/windowManager/useWindowManager.ts:439)).
Bridging watchers регулярно вызывают `wm.open(singleton=reuse)` для апдейта `data/anchor/constraints`,
и тем самым поднимают `z`. Поведение закреплено тестом: [`useWindowManager.test.ts:33-56`](simulator-ui/v2/src/composables/windowManager/useWindowManager.test.ts:33).

**Последствия:**
- Визуальный «скачок» слоя (особенно когда одновременно открыты inspector + interact).
- Сложнее поддерживать ментальную модель: «что сейчас активное и почему».

**Способ воспроизведения:**
1. Открыть NodeCard + interact-panel.
2. Дождаться автообновления данных (SSE/TTL) или вызвать действие, которое приводит к watch→`wm.open()`.
3. Наблюдать, что окно может подняться по z-order без user intent.

**Критичность:** P1 (повышено с P2 — это root cause для UX-8 и UX-9 ниже)

**Вариант исправления:**
- Добавить в WM API режим upsert без фокуса: `open({ ..., focus: 'auto' | 'always' | 'never' })`.
- В bridging watchers использовать `focus: 'never'` для реактивных апдейтов.
- Фокус/активация остаются user-driven: pointerdown на окне → `wm.focus()`.

См. также: [8.2 wm.open семантика upsert](#82-wmopen-семантика-upsert), [9. Типология — Z-order jump](#9-типология-perceived-jumps)

---

#### UX-8: wm.open() unconditional focus при singleton=reuse → инспектор всплывает поверх interact-panel

**Описание:** При обновлении данных/anchor через bridging watcher, `wm.open()` в reuse ветке
безусловно вызывает `focus(id)`, что поднимает z-order обновляемого окна. Это приводит к тому,
что inspector-окно (NodeCard/EdgeDetail) всплывает поверх interact-panel при каждом реактивном апдейте.

**Первопричина:** [`useWindowManager.ts:391-399`](simulator-ui/v2/src/composables/windowManager/useWindowManager.ts:391) — reuse ветка `open()` делает `focus(id)` всегда.

**Ссылки на код:**
- Reuse + focus: [`useWindowManager.ts:391-399`](simulator-ui/v2/src/composables/windowManager/useWindowManager.ts:391)
- Тест подтверждает поведение: [`useWindowManager.test.ts:33-56`](simulator-ui/v2/src/composables/windowManager/useWindowManager.test.ts:33)
- Interact panel watcher (триггерит open): [`SimulatorAppRoot.vue:451-511`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:451)
- NodeCard watcher (триггерит open): [`SimulatorAppRoot.vue:587-613`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:587)

**Способ воспроизведения:**
1. Открыть interact-panel + NodeCard (оба видны)
2. Anchor или phase меняется → `wm.open()` повторно вызывается из watcher
3. Inspector всплывает поверх interact-panel

**Критичность:** P1 (Medium/High)

**Вариант исправления:**
- API `wm.open({focus: 'auto' | 'always' | 'never'})`
- Watchers ставят `focus: 'never'` для реактивных апдейтов
- `focus: 'auto'` = фокус только при создании нового окна (default для нового API)

---

#### UX-9: NodeCard anchor drift при pan/zoom → перетягивает z-order

**Описание:** NodeCard watcher зависит от `selectedNodeScreenCenter`, который пересчитывается
при всех camera changes (pan/zoom). Это вызывает повторные `wm.open()` вызовы, каждый из которых
поднимает z-order инспектора.

**Первопричина:** NodeCard watcher ([`SimulatorAppRoot.vue:587-613`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:587))
реагирует на `selectedNodeScreenCenter`, который зависит от camera transform.

**Ссылки на код:**
- NodeCard watcher: [`SimulatorAppRoot.vue:587-613`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:587)
- Anchor priority: [`SimulatorAppRoot.vue:432-450`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:432)

**Способ воспроизведения:**
1. Открыть NodeCard + interact-panel
2. Pan/zoom камеру
3. Инспектор постоянно всплывает сверху interact-panel

**Критичность:** P2 (Medium)

**Вариант исправления:**
- Разделить focus и update — `wm.open({focus: 'never'})` при anchor-only updates
- Заморозить anchor инспектора при активной interact-panel (не обновлять позицию при camera changes)
- Или: throttle anchor updates для NodeCard при pan/zoom (50-100ms)

**Статус:** ✅ Done

**Результат (implementation notes):**
- Добавлен throttle для «anchor follow» NodeCard: при camera changes допускается не более **1 вызова `wm.open()` на 100ms**, чтобы избежать лишних upsert/focus и визуального «всплытия» инспектора.

**Где в коде:**
- Throttle/ограничение частоты обновлений anchor: [`SimulatorAppRoot.vue`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:1)

**Тесты (DoD):**
- Тест с fake timers, проверяющий throttling и ограничение количества `wm.open()` вызовов: [`SimulatorAppRoot.interact.test.ts`](simulator-ui/v2/src/components/SimulatorAppRoot.interact.test.ts:1)

---

### 3.2 Архитектурные проблемы

#### ARCH-1: Дуальный рендер-путь (Legacy vs WM) — закрыто (WM-only runtime)

**Статус:** WM-only runtime. Dual-path и feature-flag для отката удалены.

**Примечание:** историческая разметка legacy UI сохраняется только как статический референс (см. `legacy-windows-reference`).

---

#### ARCH-2: Монолитный SimulatorAppRoot.vue (1600+ строк)

**Описание:** Один файл содержит:
- WM bridging watchers (4+ watchers по ~40-60 строк каждый)
- ESC/keyboard handling
- Template с дуальным рендером
- EdgeDetail keepAlive/suppressed логика
- Interact flow callbacks
- TopBar context wiring
- Physics/layout wiring

**Первопричина:** Эволюционное наращивание — каждый feature добавлял watchers и refs в корневой компонент.

**Файл:** [`SimulatorAppRoot.vue`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:1)

**Последствия:**
- Сложно отслеживать зависимости между watchers
- Высокий cognitive load при code review
- Watchers могут конфликтовать (circular updates)
- Трудно unit-тестировать отдельные аспекты

**Критичность:** P2

**Вариант исправления:**
- Выделить `useWmBridging()` composable: WM watchers + ref-флаги
- Выделить `useWmEscHandling()`: ESC + keyboard logic
- Выделить `useWmEdgeDetail()`: keepAlive/suppressed/frozen logic
- Оставить SimulatorAppRoot как "тонкий" wiring layer

---

#### ARCH-3: Отсутствие единого state machine для окон

**Описание:** Состояние окон размазано между:
- `windowsMap` (WM source of truth)
- `wmEdgeDetailSuppressed`, `wmEdgeDetailKeepAlive` (ad-hoc flags)
- `wmNodeCardId`, `wmEdgeDetailId` (id tracking)
- `interactPhase` (FSM phase)
- `wmPanelOpenAnchor`, `wmEdgePopupAnchor` (anchor sources)
- `wmEdgeDetailSelectionKey` (dedup key)

**Первопричина:** WM не управляет "зачем" окно открыто — только "как" оно позиционировано. Бизнес-логика (should window be open?) живёт в watchers.

**Файлы:**
- [`SimulatorAppRoot.vue`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:138) — wmEdgeDetailId
- [`SimulatorAppRoot.vue`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:145) — wmEdgeDetailSuppressed
- [`SimulatorAppRoot.vue`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:151) — wmEdgeDetailKeepAlive
- [`SimulatorAppRoot.vue`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:393) — wmPanelOpenAnchor

**Последствия:**
- Трудно понять текущее "состояние" оконной системы
- Flags могут рассинхронизироваться (suppressed=true, но окно не существует)
- Нет единого места для отладки

**Критичность:** P2

**Вариант исправления:**
- WindowController: слой поверх WM, управляющий "бизнес-логикой" окон
- Единый state: `{inspector: {type, nodeId?, fromPid?, suppressed?, keepAlive?}, interact: {panel, phase}}`
- Watchers деривируют desired state → controller применяет diff к WM

---

#### ARCH-4: Циклические зависимости watchers

**Описание:** Watchers в SimulatorAppRoot создают циклические цепочки:

```
interactPhase changes
  → watcher [465] fires
    → wm.open() → windowsMap mutates
      → (если watchEffect) → re-trigger
```

**Первопричина:** `wm.open()` мутирует `reactive(Map)`, что может trigger watchers, читающих WM state. Комментарии в коде ([:460-463](simulator-ui/v2/src/components/SimulatorAppRoot.vue:460)) явно предупреждают: "use `watch` instead of `watchEffect`".

**Файлы:**
- [`SimulatorAppRoot.vue`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:460) — warning comment
- [`SimulatorAppRoot.vue`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:510) — same warning

**Последствия:**
- `watchEffect` вместо `watch` приводит к recursive update loop
- Текущий код использует `watch` (корректно), но refactoring может случайно переключить
- Отсутствие защиты: нет runtime detector для circular watch

**Критичность:** P3 (текущий код корректен, но хрупок)

**Вариант исправления:**
- Lint rule: запретить `watchEffect` в WM bridging code
- Или: WM operations возвращают diff, watchers применяют batch
- Или: выделить side-effect-free computed → apply в отдельном watch

---

#### ARCH-5 (Fixed): Shadow state для открытости NodeCard

**Статус:** закрыто — legacy open/close boolean ref удалён, WM остаётся единственным источником правды.

---

#### ARCH-6 (Done): Множество ad-hoc ref-флагов для EdgeDetail

**Описание:** EdgeDetail управляется 5+ отдельными ref-флагами:

| Ref | Тип | Назначение |
|-----|-----|-----------|
| `wmEdgeDetailId` | `ref<number\|null>` | ID окна в WM |
| `wmEdgeDetailSuppressed` | `ref<boolean>` | UI-close скрывает без cancel |
| `wmEdgeDetailSelectionKey` | `ref<string>` | Dedup key для selection change |
| `wmEdgeDetailKeepAlive` | `ref<boolean>` | Freeze при переходе в payment |
| `wmEdgeDetailFrozenLink` | `ref<GraphLink\|null>` | Frozen data для keepAlive |

**Первопричина:** EdgeDetail имеет сложный lifecycle:
1. Показывается в `editing-trustline` фазе
2. UI-close скрывает НЕ отменяя flow
3. KeepAlive при инициации payment из edge
4. Разные sources: interact state vs direct edge click

**Файлы:**
- [`SimulatorAppRoot.vue`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:138) — wmEdgeDetailId
- [`SimulatorAppRoot.vue`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:145) — wmEdgeDetailSuppressed
- [`SimulatorAppRoot.vue`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:146) — wmEdgeDetailSelectionKey
- [`SimulatorAppRoot.vue`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:151) — wmEdgeDetailKeepAlive
- [`SimulatorAppRoot.vue`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:152) — wmEdgeDetailFrozenLink

**Последствия:**
- Трудно понять текущее состояние EdgeDetail
- Баги при неправильной комбинации флагов
- Нет инварианта: suppressed + keepAlive одновременно?

**Критичность:** P2

**Вариант исправления:**
- Объединить в единый state object:
```ts
type EdgeDetailState =
  | { mode: 'closed' }
  | { mode: 'live'; winId: number }
  | { mode: 'suppressed'; winId: null }
  | { mode: 'keepAlive'; winId: number; frozenLink: GraphLink }
```
- Выделить в `useWmEdgeDetail()` composable

**Статус:** ✅ Done

**Результат:**
- EdgeDetail lifecycle вынесен в composable-state-machine [`useWmEdgeDetail()`](simulator-ui/v2/src/composables/useWmEdgeDetail.ts:1) (вместо набора разрозненных ref-флагов).
- Добавлены unit-тесты на ключевые state transitions (suppressed/keepAlive/live/closed).

**Где в коде:**
- State machine composable: [`useWmEdgeDetail.ts`](simulator-ui/v2/src/composables/useWmEdgeDetail.ts:1)
- Интеграция в runtime: [`SimulatorAppRoot.vue`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:1)

**Тесты (DoD):**
- Unit tests transitions: [`useWmEdgeDetail.test.ts`](simulator-ui/v2/src/composables/useWmEdgeDetail.test.ts:1)

---

#### ARCH-7 / UX: keepAlive EdgeDetail замораживает link, но не UI-контекст (context drift)

**Описание:** В keepAlive режиме EdgeDetail использует `frozenLink` для метрик (used/limit/etc),
но компонент EdgeDetailPopup строит заголовок и часть UI по `InteractState` (`state.fromPid/toPid`).
Если Interact state меняется (например, старт payment/смена выбранных pid), заголовок может
стать несогласованным с замороженным ребром.

**Первопричина:** keepAlive «замораживает» только `wmEdgeDetailFrozenLink` ([`SimulatorAppRoot.vue:1054-1056`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:1054)),
но props EdgeDetailPopup берутся из `interact.mode.state` ([`SimulatorAppRoot.vue:1353-1373`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:1353)).
При Send Payment делается `cancel() + startPaymentFlow` — edge-detail остаётся как keepAlive контекст,
а `fromPid/toPid` уже от payment flow.

**Ссылки на код:**
- Заморозка link: [`SimulatorAppRoot.vue:1054-1056`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:1054)
- Props из interact state: [`SimulatorAppRoot.vue:1353-1373`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:1353)
- EdgeDetail watcher с keepAlive: [`SimulatorAppRoot.vue:523-585`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:523)

**Последствия:**
- Неверный title (`from → to`) и/или действия выглядят относящимися к другому ребру.
- Усиливает ощущение «скачков контекста» при переходе edge-detail → payment.
- Пользователь видит "чужие" данные в open окне — серьёзная UX-проблема.

**Способ воспроизведения (конкретный):**
1. Открыть edge-detail для ребра A→B.
2. Нажать "Send Payment" — включается keepAlive, запускается payment flow.
3. Edge-detail показывает заголовок от нового payment flow (fromPid/toPid от payment), а не от замороженного A→B.

**Критичность:** P1 (High) — повышено с P2 по результатам ревью

**Вариант исправления:**
- Передавать в EdgeDetailPopup "синтетический state" из `win.data`, а не из `interact.mode.state`
- Явно заморозить UI-контекст вместе с link: сохранять `fromPid/toPid/selectedEdgeKey` в keepAlive state
- Или передавать EdgeDetailPopup отдельные пропсы `titleFromPid/titleToPid` из frozen data,
  не зависящие от живого `InteractState`

См. также: [9. Типология perceived jumps — Context drift](#9-типология-perceived-jumps)

---

### 3.3 Производительность

#### PERF-1: Двухпроходное позиционирование NodeCard (Legacy)

**Статус:** закрыто — legacy runtime-путь удалён (WM-only).

---

#### PERF-2 (Done): ResizeObserver → updateMeasuredSize → reclamp цепочка

**Описание:** Каждое изменение размера контента вызывает:
```
ResizeObserver callback
  → emit('measured')
    → wm.updateMeasuredSize()    [useWindowManager.ts:209]
      → win.measured = size
    → wm.reclamp()               [useWindowManager.ts:220]
      → clamp calculations
      → win.rect update
        → Vue reactivity
          → WindowShell re-render
            → style recalculation
```

**Первопричина:** Архитектурно верно, но в pathological cases (loading participants, animated content) может trigger 5-10 resize callbacks за секунду.

**Файлы:**
- [`WindowShell.vue`](simulator-ui/v2/src/components/WindowShell.vue:81) — ResizeObserver setup
- [`useWindowManager.ts`](simulator-ui/v2/src/composables/windowManager/useWindowManager.ts:209) — updateMeasuredSize
- [`useWindowManager.ts`](simulator-ui/v2/src/composables/windowManager/useWindowManager.ts:220) — reclamp

**Последствия:**
- Потенциальные layout thrashing при rapid content changes
- Visible position adjustments при каждом resize step

**Критичность:** P3

**Вариант исправления:**
- Debounce ResizeObserver emissions (16ms — один frame)
- Skip reclamp если позиция не изменилась
- requestAnimationFrame batching для updateMeasuredSize + reclamp

**Статус:** ✅ Done

**Результат:**
- ResizeObserver emissions coalesce на `setTimeout(16ms)` с trailing-значением (≤ 1 update/16ms).
- Skip no-op writes: `updateMeasuredSize()` не пишет при неизменных размерах; `reclamp()` early-return если геометрия не изменилась.

**Где в коде:**
- Coalesce ResizeObserver: [`WindowShell.vue`](simulator-ui/v2/src/components/WindowShell.vue:1)
- No-op guards (`updateMeasuredSize()`, `reclamp()`): [`useWindowManager.ts`](simulator-ui/v2/src/composables/windowManager/useWindowManager.ts:1)

**Тесты:**
- Coalesce (10 RO → 1 measured/reclamp после 16ms + trailing value): [`WindowShell.test.ts`](simulator-ui/v2/src/components/WindowShell.test.ts:1)
- No-op writes отсутствуют: [`useWindowManager.test.ts`](simulator-ui/v2/src/composables/windowManager/useWindowManager.test.ts:1)

---

#### PERF-3: Множественные watchers в SimulatorAppRoot

**Описание:** MinImal 4 тяжёлых watchers, каждый с deep dependency lists:

| Watcher | Строка | Dependencies | Side Effects |
|---------|--------|-------------|-------------|
| Interact→WM | [:465](simulator-ui/v2/src/components/SimulatorAppRoot.vue:465) | 6 deps | wm.open/closeGroup |
| EdgeDetail→WM | [:513](simulator-ui/v2/src/components/SimulatorAppRoot.vue:513) | 8 deps (object) | wm.open/close + flag updates |
| NodeCard→WM | [:581](simulator-ui/v2/src/components/SimulatorAppRoot.vue:581) | 5 deps (object) | wm.open/close |
| Viewport RO | [:753](simulator-ui/v2/src/components/SimulatorAppRoot.vue:753) | ResizeObserver | setViewport + reclampAll |

**Первопричина:** Каждый тип окна требует свой watcher для bridging.

**Последствия:**
- Каждый watchers fire создаёт reactive flush
- В worst case: одно событие (phase change) triggers все 4 watchers
- Избыточные wm.open() вызовы (singleton=reuse makes them cheap, но не free)

**Критичность:** P3

**Вариант исправления:**
- Объединить в один watcher с computed desired state
- `desiredWindows = computed(() => deriveFromAllState())`
- `watch(desiredWindows, (next, prev) => { applyDiff(prev, next) })`

---

#### PERF-4 (Done): Hardcoded размеры vs dynamic content

**Описание:** Оценочные размеры (estimated sizes) не соответствуют реальному контенту:

| Окно | Estimated | Типичный реальный | Дельта |
|------|----------|-----------------|--------|
| node-card (no interact) | 360×260 | ~340×200 | 20×60 |
| node-card (interact mode) | 360×260 | ~340×380 | 20×(-120) |
| interact-panel (picking) | 560×420 | ~560×280 | 0×140 |
| interact-panel (confirm) | 560×420 | ~560×430 | 0×(-10) |
| interact-panel (loading) | 560×420 | ~560×100 | 0×320 |
| edge-detail | 420×320 | ~400×250 | 20×70 |

**Первопричина:** [`getConstraints()`](simulator-ui/v2/src/composables/windowManager/useWindowManager.ts:143) возвращает статические preferred sizes, не учитывающие текущую фазу, наличие interact mode, количество trustlines и т.д.

**Последствия:**
- Первый кадр "скачет" к реальному размеру
- Cascade positioning может сместить в неожиданном направлении
- frameless mode минимизирует проблему (min-width/min-height), но не устраняет

**Критичность:** P2

**Вариант исправления:**
- Phase-aware constraints factory:
  ```ts
  getConstraints('interact-panel', { panel: 'payment', phase: 'picking-from' })
    → preferredHeight = 280
  ```
- Content-aware caching: запоминать последний measured size для каждой комбинации (type, phase)
- CSS `contain: layout` + `content-visibility: auto` для уменьшения видимого скачка

**Статус:** ✅ Done

**Результат (implementation notes):**
- `getConstraints()` стал phase-aware для `interact-panel`: `preferredHeight` зависит от `phase` (группы `picking-*` / `confirm-*` / `loading-*`).
- Цель: уменьшить mismatch `estimated` vs `measured` для первого кадра и снизить perceived jumps при открытии.

**Где в коде:**
- Phase-aware constraints: [`useWindowManager.ts`](simulator-ui/v2/src/composables/windowManager/useWindowManager.ts:1)

**Тесты (DoD):**
- Unit test на разные `preferredHeight` для фаз: [`useWindowManager.test.ts`](simulator-ui/v2/src/composables/windowManager/useWindowManager.test.ts:1)

---

### 3.4 Race conditions

#### RACE-1 (Done): Rapid node clicks → watcher coalescing

**Описание:** При быстрых кликах по нодам A→B→C Vue coalesce watcher triggers. Но `uiOpenOrUpdateNodeCard` вызывается синхронно из canvas handler, вне watcher.

**Первопричина:**
- Canvas click handler: синхронный вызов `wm.open()` в [`uiOpenOrUpdateNodeCard`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:213)
- Нет отдельного "NodeCard watcher"; окно открывается/обновляется напрямую из dblclick handler в `useSimulatorApp`.

**Файлы:**
- [`useCanvasInteractions.ts`](simulator-ui/v2/src/composables/useCanvasInteractions.ts:92) — dblclick handler
- [`SimulatorAppRoot.vue`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:213) — uiOpenOrUpdateNodeCard

**Последствия:**
- A→B→C rapid dblclick: 3 вызова `wm.open(singleton=reuse)` подряд → transient reposition/focus

**Способ воспроизведения:**
1. Rapid dblclick на 3 разные ноды за 200ms
2. Наблюдать промежуточные позиции окна

**Критичность:** P3

**Вариант исправления:**
- Debounce canvas dblclick (150ms)
- Или: cancel предыдущий open если новый приходит в том же tick

**Статус:** ✅ Done

**Результат:**
- Canvas dblclick debounced на 150ms: при серии rapid dblclick применяется только последняя нода (last intent wins).

**Где в коде:**
- Debounce dblclick intent: [`useCanvasInteractions.ts`](simulator-ui/v2/src/composables/useCanvasInteractions.ts:1)

**Тесты (DoD):**
- Unit test: rapid dblclicks → применяется только последняя нода: [`useCanvasInteractions.test.ts`](simulator-ui/v2/src/composables/useCanvasInteractions.test.ts:1)

---

#### RACE-2: ESC during async operations

**Описание:** ESC может прийти во время async операции в interact mode (payment sending, clearing running).

**Первопричина:** [`handleEsc()`](simulator-ui/v2/src/composables/windowManager/useWindowManager.ts:444) не проверяет `busy` state перед close. Policy [`onClose`](simulator-ui/v2/src/composables/windowManager/useWindowManager.ts:96) вызывает `interact.mode.cancel()`, который инкрементирует epoch — но async операция может ещё быть in-flight.

**Файлы:**
- [`useWindowManager.ts`](simulator-ui/v2/src/composables/windowManager/useWindowManager.ts:468) — ESC close
- [`useInteractMode.ts`](simulator-ui/v2/src/composables/useInteractMode.ts:424) — epoch-based cancellation

**Последствия:**
- Payment может быть отправлен, но UI показывает idle
- Или: cancel() отменяет pending, но backend уже получил request
- Epoch guard в `runBusy` предотвращает stale callback, но не отменяет HTTP

**Критичность:** P2

**Вариант исправления:**
- Busy-guard в ESC handler: если interact.mode.busy → confirm dialog или ignore ESC
- AbortController для submit-операций через [`runBusy()`](simulator-ui/v2/src/composables/useInteractMode.ts:1) / [`signal`](simulator-ui/v2/src/composables/useInteractMode.ts:1), привязанный к epoch (✅ реализовано — см. RACE-4)
- Или: escBehavior → 'ignore' когда busy=true (dynamic policy)

---

#### RACE-3: Concurrent wm.open() из разных watchers

**Описание:** Два watchers могут fire одновременно (в одном Vue flush) и оба вызвать `wm.open()`.

**Первопричина:** Watchers [`[:465]`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:465) (interact) и [`[:513]`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:513) (edge-detail) имеют overlapping triggers (interactPhase, isFullEditor).

При переходе `editing-trustline` → `isFullEditor=true`:
- Watcher 1: interactWindowOfPhase → `{type:'interact-panel', panel:'trustline'}`
- Watcher 2: shouldShow = false (isFullEditor=true) → close edge-detail

Обе операции происходят в одном tick.

**Файлы:**
- [`SimulatorAppRoot.vue`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:465) — interact watcher
- [`SimulatorAppRoot.vue`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:513) — edge-detail watcher

**Последствия:**
- `closeGroupExcept` в `wm.open()` может закрыть окно, которое другой watcher ещё не обработал
- В текущем коде: `singleton=reuse` и `closeGroup` делают это безопасным (идемпотентно)
- Но рефакторинг может нарушить этот инвариант

**Критичность:** P3 (текущий код безопасен)

**Вариант исправления:**
- Batch WM operations within a tick
- Единый watcher для всех window decisions (см. PERF-3)

---

#### RACE-4 (Done): Stale epoch в runBusy

**Описание:** `runBusy` использует epoch-based cancellation guard в [`useInteractMode.ts`](simulator-ui/v2/src/composables/useInteractMode.ts). При быстром cancel+restart новый runBusy может начаться до завершения предыдущего.

**Первопричина:** `cancel()` инкрементирует epoch, но не await'ит текущий async. Если пользователь быстро: Start Payment → ESC → Start Payment — два runBusy могут быть in-flight.

**Последствия:**
- Первый runBusy продолжает execution, но его callbacks ignored (epoch check)
- Второй runBusy может прочитать stale cache data (optimistic UI)
- В pathological case: двойной payment submit

**Критичность:** P2

**Вариант исправления:**
- AbortController per runBusy, cancel при epoch mismatch
- Lock: новый runBusy ожидает завершения предыдущего
- Idempotency на backend (уже есть для payments — idempotency key)

**Статус:** ✅ Done

**Результат (implementation notes):**
- `AbortController` привязан к **epoch** и используется в submit-пайплайнах, которые идут через [`runBusy()`](simulator-ui/v2/src/composables/useInteractMode.ts:1) / [`signal`](simulator-ui/v2/src/composables/useInteractMode.ts:1): каждый запуск async runner в interact mode получает свой abort-signal.
- `cancel()` теперь **abort’ит активный HTTP request** текущего epoch **только для submit-операций**, выполняемых через [`runBusy()`](simulator-ui/v2/src/composables/useInteractMode.ts:1) / [`signal`](simulator-ui/v2/src/composables/useInteractMode.ts:1).
- Фоновые загрузки/рефетчи (не проходящие через `runBusy`/`signal`) продолжают полагаться на epoch-guard (stale-ответы **игнорируются**), без попытки abort — это допустимо в рамках текущего scope.
- Добавлен тест на сценарий **cancel → restart**.

**Где в коде:**
- Epoch runner + отмена: [`useInteractMode.ts`](simulator-ui/v2/src/composables/useInteractMode.ts:1)
- Обвязка действий (проброс cancel/restart semantics): [`useInteractActions.ts`](simulator-ui/v2/src/composables/useInteractActions.ts:1)
- HTTP слой с abort signal: [`simulatorApi.ts`](simulator-ui/v2/src/api/simulatorApi.ts:1)

**Тесты (DoD):**
- Unit test cancel→restart: [`useInteractMode.test.ts`](simulator-ui/v2/src/composables/useInteractMode.test.ts:1)

---

## 4. Анализ ресайзов

### 4.1 Где размер вычисляется поздно

```
Timeline открытия interact-panel:

T=0ms    wm.open() вызван
         estimateSizeFromConstraints → 560×420
         WindowShell рендерится с min-width:320, min-height:220
         (frameless mode — нет inline width/height)
         
T=0-16ms Vue reactive flush → DOM mount
         WindowShell.onMounted → ResizeObserver created
         
T=16ms   First frame painted (browser)
         Пользователь видит: оболочку с estimated позицией
         Контент: loading spinner (маленький)
         Реальный размер: ~560×100
         
T=16-32ms ResizeObserver callback fires
          measured = {560, 100}  ← ПЕРВОЕ ИЗМЕРЕНИЕ
          updateMeasuredSize → win.measured set
          reclamp → позиция может измениться
          → СКАЧОК #1 (estimated 420 → measured 100 по высоте)
          
T=100-500ms Participants loaded (async)
            Panel content grows: ~560×100 → ~560×350
            ResizeObserver fires again
            measured = {560, 350}
            reclamp → позиция может СНОВА измениться
            → СКАЧОК #2 (panel "вырос")
            
T=500ms+   Content stable
           Нет более скачков (если данные не меняются)
```

### 4.2 Какие скачки видит пользователь

| Скачок | Причина | Видимость | Частота |
|--------|---------|-----------|---------|
| Position jump при opening | estimated vs measured position | Заметный | Каждое открытие |
| Height shrink (estimated → loading) | preferredHeight=420, real~100 | Заметный | Каждое новое opening |
| Height grow (loading → loaded) | Content appears | Заметный | Каждое первое opening |
| Position clamp при grow | Panel exceeds viewport | Редко заметный | При panel у edges |
| Width mismatch | preferred vs CSS max-width | Минимальный | frameless minimizes |

### 4.3 estimateSizeFromConstraints vs реальный размер

[`estimateSizeFromConstraints()`](simulator-ui/v2/src/composables/windowManager/geometry.ts:9):
```ts
const w = typeof c.preferredWidth === 'number' ? c.preferredWidth : c.minWidth
const h = typeof c.preferredHeight === 'number' ? c.preferredHeight : c.minHeight
```

**Проблема:** функция возвращает ONE static size, не учитывая:
- Текущую фазу панели (loading / picking / confirm)
- Количество контента (0 participants vs 20 participants)
- Interact mode vs non-interact mode (для NodeCard)
- Наличие trustlines в NodeCard

**Рекомендация:** Phase-aware estimation:
```ts
function estimateInteractPanelSize(panel: string, phase: string): {w: number, h: number} {
  if (phase.includes('picking')) return {w: 560, h: 300}
  if (phase.includes('confirm')) return {w: 560, h: 450}
  if (phase.includes('clearing')) return {w: 560, h: 380}
  return {w: 560, h: 420} // fallback
}
```

### 4.4 (Fixed) Legacy hardcoded card sizes

**Статус:** закрыто — legacy positioning/runtime удалены, hardcoded размеры больше не применяются.

### 4.5 preferredWidth/Height constraints vs actual

[`getConstraints()`](simulator-ui/v2/src/composables/windowManager/useWindowManager.ts:143):

```
interact-panel:
  payment/clearing: preferred 560×420, min 320×220
  trustline:        preferred 380×420, min 320×220

node-card:          preferred 360×260, min 320×180
edge-detail:        preferred 420×320, min 340×200
```

[`WindowShell.vue`](simulator-ui/v2/src/components/WindowShell.vue:37) в frameless mode:
```ts
if (props.frameless) {
  return {
    ...base,       // left, top, zIndex
    minWidth: c.minWidth + 'px',    // 320px
    minHeight: c.minHeight + 'px',  // 220px (interact) или 180px (node-card)
  }
}
```

**ВАЖНО:** В frameless mode:
- `width` и `height` НЕ устанавливаются в inline style
- Размер определяется контентом + minWidth/minHeight constraints
- preferred sizes используются ТОЛЬКО для estimated position (cascadeShift, anchor offset)
- После first ResizeObserver → measured size заменяет estimated
- Первый видимый кадр зависит от CSS контента, а не от preferred

**Рекомендация:** Минимальный скачок достигается когда:
1. minWidth/minHeight близки к реальному контенту (рабочий скелетон)
2. Или: pre-render в `visibility:hidden` → измерить → показать
3. Или: CSS `contain: size` + фиксированный skeleton

### 4.6 Стратегия первого кадра (pre-measure)

Текущая стратегия:
```
1. estimated position = anchor + offset(16,16) + cascadeShift
2. estimated size = preferred (для collision avoidance только)
3. DOM mount → frameless → min-width/min-height → content determines actual
4. ResizeObserver → measured → reclamp (может сдвинуть)
```

**Проблемы:**
- Шаг 3→4 создаёт видимый скачок
- Между mount и RO callback есть 1 frame gap

**Предложения по улучшению:**

#### A. Offscreen pre-measurement
```
1. Render WindowShell в offscreen container (position:fixed, left:-9999px)
2. Дождаться first RO callback → get real size
3. Position с реальным размером
4. Move to final position → make visible
```
Pros: нулевой видимый скачок
Cons: дополнительный render cycle, может задержать открытие на ~16-32ms

#### B. Content-size caching
```
1. Первое открытие → estimated → RO → cache measured size per (type, phase)
2. Последующие открытия → cache hit → use cached size for estimation
3. RO всё ещё корректирует, но delta минимальна
```
Pros: простая реализация, улучшается с каждым использованием
Cons: первое открытие всё ещё скачет, cache invalidation нужна при resize

#### C. Predictive sizing с skeleton
```
1. Каждый panel/card определяет skeleton height для каждой фазы
2. Skeleton рендерится до загрузки контента
3. Skeleton height ≈ loaded height → минимальный скачок
```
Pros: предсказуемый UX, стабильные тесты
Cons: maintenance overhead, нужно обновлять при UI changes

#### D. CSS content-visibility: auto
```
1. Контент рендерится с content-visibility: auto
2. Браузер может определить layout size до paint
3. Снижает reflow cost, но не устраняет скачок полностью
```
Pros: минимальный код, нативная оптимизация
Cons: не решает проблему загрузки async данных

**Рекомендация:** Комбинация B + C.
- Caching measured sizes для повторных открытий
- Skeleton placeholders для предсказуемого первого open
- RO как safety net для edge cases

### 4.7 Что уже хорошо защищено от ресайз-скачков

> Добавлено по результатам ревью v2 — признание существующих защит.

**1. preferredWidth совпадает с CSS-ограничениями панелей:**
- Payment/Clearing: `preferredWidth=560` совпадает с `.ds-ov-panel` CSS max-width ([`useWindowManager.ts:147-165`](simulator-ui/v2/src/composables/windowManager/useWindowManager.ts:147))
- Trustline: `preferredWidth=380` соответствует CSS-ширине ([`TrustlineManagementPanel.vue:268-307, 492-499`](simulator-ui/v2/src/components/TrustlineManagementPanel.vue:268))
- Это означает, что **по ширине** первый estimate практически всегда совпадает с реальным — скачка ширины нет

**2. reclamp после measured НЕ переснэпивает если окно в bounds:**
- [`useWindowManager.ts:220-309`](simulator-ui/v2/src/composables/windowManager/useWindowManager.ts:220) — post-measurement reclamp
- Если окно после измерения всё ещё внутри viewport → позиция не меняется
- Snap8 НЕ применяется повторно → нет микро-сдвигов

**3. Регрессионные тесты на "stub→full рост без прыжка":**
- [`useWindowManager.test.ts:169-236`](simulator-ui/v2/src/composables/windowManager/useWindowManager.test.ts:169) — тесты проверяют что при росте контента из stub до полного размера позиция стабильна (если в bounds)

**4. Legacy позиционирование панелей уже корректно:**
- [`ManualPaymentPanel.vue:73-98`](simulator-ui/v2/src/components/ManualPaymentPanel.vue:73) — anchor + hostEl
- [`TrustlineManagementPanel.vue:268-309`](simulator-ui/v2/src/components/TrustlineManagementPanel.vue:268) — useOverlayPositioning
- [`ClearingPanel.vue:23-63`](simulator-ui/v2/src/components/ClearingPanel.vue:23) — placeOverlayNearAnchor
- [`EdgeDetailPopup.vue:63-101`](simulator-ui/v2/src/components/EdgeDetailPopup.vue:63) — anchor positioning

### 4.8 Оставшиеся риски ресайзов (уточнённые)

> Обновлено по результатам ревью v2 — с учётом того что уже защищено.

**1. Anchor race / 2-step open:**
- Если первый `wm.open()` случается с `anchor=null`, а второй — с правильным anchor, окно "телепортируется"
- Риск остаётся где anchor появляется "позже" / асинхронно (например, edge popup anchor)
- Связано с anchor priority: edge-popup anchor > panel-open anchor > legacy panelAnchor ([`SimulatorAppRoot.vue:432-450`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:432))

**2. Content growth → out-of-bounds clamp:**
- Если окно растёт (loading → loaded) и вылезает за viewport, `reclamp()` прижимает его
- Это создаёт заметный сдвиг, особенно у нижней/правой границы viewport
- Oговорка: в frameless WM размер задаёт контент, estimate влияет только на геометрию WM — скачок происходит только при выходе за bounds

**3. Z-order jump ≠ geometry jump, но perceived как "скачок":**
- `open()` при `singleton='reuse'` всегда делает `focus(id)` → окно "всплывает" по z
- Не скачок геометрии, но UX воспринимается как "прыжок слоя" (см. UX-7, UX-8, UX-9)

**4. Transients при быстрых cancel→start:**
- Phase меняется раньше чем anchor/selection стабилизировались
- Частично закрыто `wmPanelOpenAnchor` ([`SimulatorAppRoot.vue:432-450`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:432))
- Repro: быстро кликать edge-detail actions / быстро менять участников

---

## 5. План рефакторинга

### 5.1 Целевая архитектура

```
┌───────────────────────────────────────────────────────┐
│                SimulatorAppRoot.vue                     │
│  (тонкий wiring: provide/inject, template slots only)  │
└────────┬──────────────────────────────────────────────┘
         │
    ┌────▼──────────────────────────────────────────────┐
    │          useWindowController()                      │
    │  ― Единый derived state для всех окон              │
    │  ― desiredWindows = computed(() => ...)             │
    │  ― Один watch: diff desired vs actual → WM ops     │
    │  ― ESC handling                                    │
    │  ― Focus management                               │
    └────────┬──────────────────────────────────────────┘
             │
        ┌────▼──────────────────────────────────┐
        │        useWindowManager()               │
        │  ― Pure WM: position, z-order, resize  │
        │  ― No business logic                   │
        │  ― windowsMap, open/close/reclamp      │
        └────────┬──────────────────────────────┘
                 │
            ┌────▼──────────────────────────┐
            │     WindowShell.vue             │
            │  ― Geometry wrapper (frameless) │
            │  ― ResizeObserver              │
            │  ― Focus-trap (optional)       │
            └────────────────────────────────┘
```

### 5.2 Этапы внедрения

#### Этап 1: Стабилизация (P0/P1 issues)

1. **Исправить hard dismiss canvas-click (UX-4)**
  - Canvas-click закрывает inspector и отменяет interact flow (hard dismiss)
  - (Без confirm) — клик в пустоту трактуется как явное “выйти из контекста”

2. **Добавить closing state для ESC (UX-3)**
   - Closing state / deferred delete вместо debounce (см. Decision 8.3)
   - Окно в `closing` state пропускается при поиске topmost для ESC

3. **Busy-guard для ESC (RACE-2)**
   - Если `interact.mode.busy` → показать inline confirm или ignore ESC
   - Убрать risk of cancel during payment execution

#### Этап 2: Рефакторинг EdgeDetail (ARCH-6)

1. **Выделить `useWmEdgeDetail()` composable**
   - Объединить 5 ref-флагов в один state machine
   - Переместить watcher из SimulatorAppRoot
   - Экспортировать: `{effectiveLink, effectivePhase, effectiveBusy, openEdge, closeEdge}`

2. **Тест coverage**
   - Unit tests для state transitions
   - Integration tests для suppressed/keepAlive flows

#### Этап 3: Рефакторинг window bridging (ARCH-2, ARCH-3)

1. **Создать `useWindowController()`**
  - Input: interactPhase, selectedNode, WM windows state, etc.
   - Output: desiredWindows computed
   - Один watch: `desiredWindows → applyDiffToWM()`

2. **Убрать множественные watchers из SimulatorAppRoot**
  - Заменить watchers (interact-panel / edge-detail) на один watch в controller

3. **Переместить `makeInteractPanelWindowData` в controller**

#### Этап 4: Устранение legacy path (ARCH-1)

1. **Удалить legacy opt-out и dual-path (закрыто)**
  - Legacy runtime-путь удалён; runtime — WM-only.
  - Исторический референс верстки фиксируется снапшотами HTML.

#### Этап 5: UX polish (P2/P3)

1. **Content-size caching**
   - Кэшировать measured size per (type, subtype, phase)
   - Использовать при первом open

2. **Skeleton placeholders**
   - Фиксированные скелетоны для loading states

3. **Focus management**
   - Focus-return stack при close
   - aria-live regions для screen readers

4. **A11y**
   - Focus-trap в framed mode
   - Role="dialog" для modal-like windows
   - aria-label, aria-describedby

### 5.3 Оценка рисков регрессий

| Этап | Риск | Митигация |
|------|------|------------|
| Этап 1 | Low: изменения изолированы | Существующие e2e тесты покрывают основные flows |
| Этап 2 | Low: refactoring composable | Unit tests + snapshot tests для EdgeDetail states |
| Этап 3 | Medium: новый controller заменяет watchers | Параллельный запуск old vs new, assertion comparing |
| Этап 4 | High: удаление legacy path | Feature flag `?legacy=1` на время перехода, rollback branch |
| Этап 5 | Low: additive changes | A/B testing для UX polish |

### 5.4 Шаги валидации

Для каждого этапа:

1. **Unit tests pass** (vitest)
   - Все существующие + новые для refactored code
   - Coverage для edge cases (rapid clicks, ESC during async, etc.)

2. **Integration tests** ([`SimulatorAppRoot.interact.test.ts`](simulator-ui/v2/src/components/SimulatorAppRoot.interact.test.ts))
   - All existing AC проходят
   - New AC для fixed issues

3. **Manual QA checklist**
   - 11 scenarios from ESC spec
   - Rapid click stress test
   - Canvas click during active flow
   - Small viewport (< 800px width)

4. **Visual regression**
   - Screenshot comparison before/after
   - Key states: empty → loading → loaded → confirm

---

## 6. Тесты и наблюдаемость

### 6.1 Набор проверок

#### Unit tests (vitest)

**WindowManager:**
```
- open() создаёт WindowInstance с correct policy
- open() singleton=reuse обновляет data без создания нового
- open() closeGroupExcept закрывает другие в группе
- close() удаляет из map + вызывает policy.onClose
- close() с reason='programmatic' НЕ вызывает interact onClose
- handleEsc() back-then-close вызывает onEsc()
- handleEsc() обрабатывает form-like targets
- handleEsc() обрабатывает nested content consumption
- reclamp() вмещает в viewport
- reclamp() НЕ snap8 после measurement
- updateMeasuredSize() игнорирует 0×0
- cascadeShiftAvoidOverlaps() сдвигает при наложении
```

**WindowShell:**
```
- frameless: no width/height in style
- frameless: applies min-width/min-height from constraints
- framed: applies width/height
- ResizeObserver: emits measured with correct size
- Transition classes applied correctly
```

**useNodeCard:**
```
- WM mode: returns {display: 'block'} only
- Legacy mode: 4-direction placement
- Legacy mode: _reclamp correction
```

**interactWindowOfPhase:**
```
- Payment phases → interact-panel payment
- Trustline phases → interact-panel trustline
- editing-trustline + fullEditor → interact-panel trustline
- editing-trustline + !fullEditor → edge-detail
- Clearing phases → interact-panel clearing
- idle → null
```

**WindowController (будущий):**
```
- desiredWindows computed для idle → empty
- desiredWindows для picking-payment-from → [{interact-panel, payment}]
- desiredWindows для editing-trustline + NodeCard → [{edge-detail}, {node-card}]
- Differential: no redundant open/close calls
```

#### Integration tests

**ESC step-back (существующие AC):**
```
- AC-1: Payment из NodeCard — 1 ESC закрывает
- AC-2: Payment из ActionBar — step-back по шагам
- AC-3: Payment из EdgeDetail — 2 ESC
- AC-4: Trustline из NodeCard — 1 ESC
- AC-5: Edit Trustline из NodeCard — 2 ESC
- AC-6: FROM dropdown включает prefilled участника
- AC-7: Clearing — нет step-back
- AC-8: Legacy ESC stack корректен
```

**Canvas-click (новые AC):**
```
- AC-C1: Canvas empty click без inspector окон → ничего не закрывается
- AC-C2: Canvas empty click при NodeCard open → NodeCard закрывается
- AC-C3: Canvas empty click при EdgeDetail open → EdgeDetail закрывается
- AC-C4: Canvas empty click при EdgeDetail + NodeCard → закрываются обе карточки
- AC-C5: Canvas empty click при активном Interact flow → flow отменяется (interact-panel закрывается)
```

**Z-order / focus-on-update (новые AC):**
```
- AC-Z1: SSE/TTL update НЕ меняет z-order окон без pointerdown
- AC-Z2: wm.open(singleton=reuse) в bridging watchers НЕ вызывает focus (после fix)
```

**EdgeDetail keepAlive context (новые AC):**
```
- AC-ED1: keepAlive фиксирует и метрики, и title/from/to (не дрейфует при изменении interact state)
```

**Rapid interactions (новые AC):**
```
- AC-R1: 5 rapid dblclicks на разные ноды → final state = last node
- AC-R2: ESC 3 раза за 200ms → корректный step-back
- AC-R3: Open Payment → ESC → Open Payment → no double trigger
```

#### E2E tests (Playwright)

```
- Workflow: open node → send payment → confirm → success
- Workflow: open edge → edit trustline → save
- Workflow: open node → send payment → ESC step-back → close
- Stress: rapid node switching (10 clicks/sec)
- Visual: no resize flash (screenshot comparison)
- A11y: focus management after close
```

### 6.2 Логирование ключевых переходов

Предлагаемые log points:

```ts
// useWindowManager.ts
open():    console.debug('[WM] open', {type, id, singleton, anchor})
close():   console.debug('[WM] close', {id, reason, type})
reclamp(): console.debug('[WM] reclamp', {id, delta: {dx, dy}})

// SimulatorAppRoot.vue (watchers)
interact watcher: console.debug('[Bridge] interact', {phase, panel, anchorKey})
nodeCard watcher: console.debug('[Bridge] nodeCard', {shouldShow, nodeId})
edgeDetail watcher: console.debug('[Bridge] edgeDetail', {shouldShow, suppressed, keepAlive})

// WindowShell.vue
measured: console.debug('[Shell] measured', {id, width, height})
```

**Условие:** под feature flag `?debug=wm` (не в production)

### 6.3 Метрики

| Метрика | Описание | Target |
|---------|---------|--------|
| `wm.time_to_first_correct_size` | Время от open() до первого RO callback | < 32ms |
| `wm.resize_count_after_show` | Количество RO callbacks после показа | ≤ 2 для static, ≤ 3 для async |
| `wm.rerender_count_per_open` | Vue re-renders от open до stable | ≤ 4 |
| `wm.position_jumps_after_show` | Видимые сдвиги позиции > 4px | 0 для static content |
| `wm.esc_double_close_rate` | % случаев двойного закрытия при rapid ESC | 0% |
| `wm.focus_return_success` | % успешных focus-return после close | > 95% |

### 6.4 Критерии приемки (тестов)

- [ ] Все 8 ESC AC проходят (unit + integration)
- [ ] Canvas-click AC проходят
- [ ] Rapid interaction AC проходят
- [ ] Coverage > 85% для WindowManager, WindowShell, WindowController
- [ ] Нет console.error в test run
- [ ] Нет Vue reactivity warnings
- [ ] Screenshot visual comparison: delta < 2% pixels

---

## 7. Критерии приемки рефакторинга

### 7.1 Функциональные критерии

| # | Критерий | Измерение | Target |
|---|---------|-----------|--------|
| F-1 | Все существующие ESC AC проходят | Integration test suite | 100% pass |
| F-2 | Canvas-click отменяет interact flow (hard dismiss) | Manual + e2e | Confirmed |
| F-3 | Edge-detail suppressed/keepAlive работают | Unit tests | All pass |
| F-4 | Singleton=reuse: data обновляется, окно не пересоздаётся | Unit test | 0 re-creates per update |
| F-5 | Dual render path удалён (WM-only runtime) | Code search + unit tests | Pass |
| F-6 | All window types open/close/reopen корректно | E2E | No orphan windows |

### 7.2 UX критерии

| # | Критерий | Измерение | Target |
|---|---------|-----------|--------|
| U-1 | Окно показывается сразу в финальном размере | Visual regression + метрика `resize_count` | ≤ 1 resize для static content |
| U-2 | Отсутствие видимых скачков позиции | Manual QA + screenshot delta | 0 position jumps > 4px для static |
| U-3 | ESC step-back: correct behavior per AC | Integration tests | 100% |
| U-4 | Canvas click: inspector closes + interact cancels | E2E test | Confirmed |
| U-5 | Rapid clicks: final state = last click intent | Stress test (10 clicks/sec) | Correct final state |
| U-6 | Фокус восстанавливается после закрытия | a11y audit | `document.activeElement` correct |
| U-7 | Анимация smooth (60fps, no jank) | Performance profiling | < 5ms per frame during transition |
| U-8 | Нет неожиданных поднятий окон по z-order на данных | Manual QA + AC-Z1 | 0 «autofocus» подъёмов |

### 7.3 Архитектурные критерии

| # | Критерий | Измерение | Target |
|---|---------|-----------|--------|
| A-1 | (Опционально) Декомпозиция SimulatorAppRoot | Code review | Out of scope / later |
| A-2 | Нет дуальных render paths | Code search | 0 legacy branching guards |
| A-3 | Единый WindowController для всех window decisions | Code review | 1 composable, 1 watch |
| A-4 | EdgeDetail state = 1 state machine (не 5 refs) | Type check | `EdgeDetailState` union type |
| A-5 | Нет shadow state для открытости NodeCard | Code search | No legacy open-boolean refs |
| A-6 | WM — единственный source of truth | Architecture review | No shadow state |

### 7.4 Стабильность

| # | Критерий | Измерение | Target |
|---|---------|-----------|--------|
| S-1 | Стабильность при rapid interactions | Stress test: 10 clicks/sec, 30 sec | No crash, no orphan windows |
| S-2 | ESC during async: no data loss | Test: ESC during payment send | Payment either completes or cleanly cancels |
| S-3 | Concurrent watchers: no circular update | Vue devtools: no "Maximum recursive updates" | 0 warnings |
| S-4 | Memory: no window instances leaked | Heap snapshot before/after 100 open/close cycles | ≤ 5% growth |
| S-5 | Нет двойных подписок ResizeObserver | RO callback count per window | Exactly 1 observer per window |

### 7.5 Производительность

| # | Критерий | Измерение | Target |
|---|---------|-----------|--------|
| P-1 | Time to first meaningful paint (окно) | Performance mark | < 50ms from click |
| P-2 | RO callback → reclamp → repaint | Performance mark | < 16ms |
| P-3 | Watcher fire → WM operation → settled | Vue devtools | < 5ms sync overhead |
| P-4 | Нет forced layout (BRC) в WM path | Performance profiling | 0 forced reflows from WM |

---

## 8. Decision Log

> Добавлено в ревизии 2.0 по результатам ревью — фиксация ключевых решений и спорных мест.

### 8.1 Canvas empty click policy

**Контекст:** Исторически в коде существовали противоречивые философии обработки клика в пустое место canvas.

| Источник | Файл / строки | Поведение |
|----------|--------------|-----------|
| Step 0 — pure policy helper | [`useSimulatorApp.ts`](simulator-ui/v2/src/composables/useSimulatorApp.ts:1) | Empty click отменяет interact flow |

**Нормативное решение:**

Empty click закрывает **обе inspector-карточки** (edge-detail + node-card) и **отменяет interact flow**.

Причина: это “hard dismiss” — снимает риск путаницы контекста (flow активен, но пользователь уже кликнул в пустоту).

---

### 8.2 wm.open семантика upsert

**Контекст:** `wm.open()` — это не только "открыть окно", но и **upsert**: обновить `data/anchor/constraints/policy` для существующего окна при `singleton='reuse'`.

**Побочные эффекты upsert (текущие):**

| Побочный эффект | Строки | Описание |
|----------------|--------|----------|
| Безусловный `focus(id)` | [`useWindowManager.ts:391-399`](simulator-ui/v2/src/composables/windowManager/useWindowManager.ts:391) | Z-order поднимается при каждом update |
| Repositioning до measurement | [`useWindowManager.ts:439-444`](simulator-ui/v2/src/composables/windowManager/useWindowManager.ts:439) | Если anchor изменился — окно "прыгает" к новому anchor |
| `closeGroupExcept` | — | Закрывает другие окна в группе (идемпотентно при reuse) |

**Проблема:** Bridging watchers вызывают `wm.open()` при каждом реактивном обновлении (SSE, TTL refetch, phase change, camera pan). Каждый вызов поднимает z-order — окно "всплывает" без user intent.

**Рекомендация API:**
```
wm.open({
  ...existing,
  focus: 'auto' | 'always' | 'never'
})
```

- `'auto'` (default) — фокус только при создании нового окна
- `'always'` — всегда поднимать z-order (user-initiated open, например из кнопки)
- `'never'` — никогда не менять z-order (для bridging watcher updates)

Watchers должны использовать `focus: 'never'` для реактивных апдейтов:
- Interact panel watcher: [`SimulatorAppRoot.vue:451-511`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:451)
- EdgeDetail watcher: [`SimulatorAppRoot.vue:523-585`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:523)
- NodeCard watcher: [`SimulatorAppRoot.vue:587-613`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:587)

---

### 8.3 ESC debounce vs closing state

**Контекст:** Первоначально предложенный debounce 180ms на ESC (UX-3) вызвал несогласие при ревью.

**Аргументы против debounce:**
- Быстрый повтор ESC часто осознанный "закрыть всё" — debounce ломает этот паттерн
- Пользователь жмёт ESC 3 раза и ожидает что все 3 окна закроются
- 180ms debounce скрывает ответ системы — ощущение "ESC не работает"

**Альтернатива — closing state / deferred delete:**

```
Текущее:
  ESC → close(id) → windowsMap.delete(id) → DOM in leave-transition
  ESC → найти next topmost → close(next) → UNWANTED

Предлагаемое:
  ESC → close(id) → win.state = 'closing' → DOM in leave-transition
  ESC → найти topmost (skip state='closing') → close(next) → CORRECT
  Transition end → windowsMap.delete(id)
```

**Рекомендация:** Closing state подход. Каждое нажатие ESC корректно "проходит" по стеку, пропуская уже закрывающиеся окна. Быстрые ESC работают как ожидается — "закрыть всё по очереди".

**Ссылки:**
- ESC dispatch: [`SimulatorAppRoot.vue:703-710`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:703), [`SimulatorAppRoot.vue:712-750`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:712)
- `geo:interact-esc` consumers: [`useDestructiveConfirmation.ts:55-121`](simulator-ui/v2/src/composables/useDestructiveConfirmation.ts:55)

---

## 9. Типология perceived jumps

> Добавлено в ревизии 2.0 — классификация "скачков", воспринимаемых пользователем.

Пользователь может воспринимать три типа "скачков" в оконной системе. Важно различать их,
так как root cause и fix для каждого типа различаются.

### 9a. Geometry jump — скачок позиции/размера

**Описание:** Окно физически перемещается или меняет размер.

**Источники:**

| Причина | Механизм | Ссылки |
|---------|---------|--------|
| Anchor race / 2-step open | Первый `wm.open()` с `anchor=null`, второй — с правильным anchor → телепортация | [`SimulatorAppRoot.vue:432-450`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:432) |
| Content growth → OOB clamp | Окно растёт (loading→loaded), вылезает за viewport → `reclamp()` прижимает | [`useWindowManager.ts:220-309`](simulator-ui/v2/src/composables/windowManager/useWindowManager.ts:220) |
| Estimated vs measured mismatch | Первый кадр с estimated size, RO callback корректирует | [`geometry.ts:9`](simulator-ui/v2/src/composables/windowManager/geometry.ts:9) |

**Уже защищено:** preferredWidth совпадает с CSS (нет скачка по ширине); reclamp не переснэпивает если в bounds.

**Метрика:** `wm.position_jumps_after_show > 4px`

---

### 9b. Z-order jump — неожиданное всплытие окна

**Описание:** Окно поднимается по z-order (становится поверх других) без user intent.
Технически позиция/размер не меняются, но визуально "что-то прыгнуло".

**Источники:**

| Причина | Механизм | Ссылки |
|---------|---------|--------|
| `open(singleton='reuse')` + `focus(id)` | Каждый watcher update вызывает `focus()` | [`useWindowManager.ts:391-399`](simulator-ui/v2/src/composables/windowManager/useWindowManager.ts:391) |
| NodeCard anchor drift при pan/zoom | Camera change → watcher → `wm.open()` → focus | [`SimulatorAppRoot.vue:587-613`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:587) |
| Phase change → interact watcher | Phase update → `wm.open()` → focus поднимает interact-panel | [`SimulatorAppRoot.vue:451-511`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:451) |

**Fix:** API `wm.open({focus: 'never'})` для watcher-driven updates.

---

### 9c. Context drift — keepAlive окно показывает "чужие" данные

**Описание:** Окно визуально на месте, размер и z-order не меняются, но содержимое
показывает данные от другого flow (несогласованность замороженного и живого state).

**Источники:**

| Причина | Механизм | Ссылки |
|---------|---------|--------|
| keepAlive замораживает link, но не fromPid/toPid | `wmEdgeDetailFrozenLink` заморожен, props из `interact.mode.state` — живые | [`SimulatorAppRoot.vue:1054-1056`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:1054), [`SimulatorAppRoot.vue:1353-1373`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:1353) |
| Переход edge-detail → payment | `cancel() + startPaymentFlow` меняет state, edge-detail в keepAlive читает новый state | ARCH-7 |

**Fix:** Передавать "синтетический state" из `win.data`, не из живого `interact.mode.state`.

---

## Приложение A: Полная карта файлов

| Файл | Строк | Роль |
|------|-------|------|
| [`useWindowManager.ts`](simulator-ui/v2/src/composables/windowManager/useWindowManager.ts:1) | 513 | Core WM: windowsMap, open/close/handleEsc |
| [`types.ts`](simulator-ui/v2/src/composables/windowManager/types.ts:1) | 118 | WindowType, WindowGroup, WindowPolicy, etc. |
| [`geometry.ts`](simulator-ui/v2/src/composables/windowManager/geometry.ts:1) | 30 | clamp, estimateSizeFromConstraints, overlaps |
| [`interactWindowOfPhase.ts`](simulator-ui/v2/src/composables/windowManager/interactWindowOfPhase.ts:1) | 44 | Phase → WindowType mapping |
| [`WindowShell.vue`](simulator-ui/v2/src/components/WindowShell.vue:1) | ~250 | Geometry wrapper, RO, animations |
| [`SimulatorAppRoot.vue`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:1) | ~1350 | Root: WM watchers, ESC, template, bridging |
| [`NodeCardOverlay.vue`](simulator-ui/v2/src/components/NodeCardOverlay.vue:1) | 359 | NodeCard UI component |
| [`useInteractFSM.ts`](simulator-ui/v2/src/composables/useInteractFSM.ts:1) | 398 | 11-phase state machine |
| [`useInteractMode.ts`](simulator-ui/v2/src/composables/useInteractMode.ts:1) | 704 | Facade: epoch-based async runner |
| [`useInteractDataCache.ts`](simulator-ui/v2/src/composables/useInteractDataCache.ts:1) | 395 | TTL-based data cache |
| [`useInteractPanelPosition.ts`](simulator-ui/v2/src/composables/useInteractPanelPosition.ts:1) | 123 | Panel anchor management |
| [`useCanvasInteractions.ts`](simulator-ui/v2/src/composables/useCanvasInteractions.ts:1) | ~200 | Click/dblclick handling |
| [`useSimulatorApp.ts`](simulator-ui/v2/src/composables/useSimulatorApp.ts:1) | 1700+ | App facade |

## Приложение B: Матрица проблем

| ID | Категория | Критичность | Этап fix | Описание | Изм. v2 |
|----|-----------|------------|---------|---------|---------|
| UX-1 | UX | P2 | Этап 5 | Видимый ресайз при открытии | — |
| UX-2 | UX/A11y | P2 | Этап 5 | Потеря фокуса при закрытии | ✅ Done |
| UX-3 | UX | P2 | Этап 1 | Closing state для transition-aware ESC | ✏️ fix пересмотрен |
| UX-4 | UX | P1 | Этап 1 | Hard dismiss canvas-click — противоречивая политика | ✏️ ссылки уточнены |
| UX-5 | UX | P3 | Этап 3 | Повторное открытие той же карточки | ✅ Done |
| UX-6 | UX | P3 | Этап 3 | Быстрые последовательные клики | ✅ Done |
| UX-7 | UX | **P1** | Этап 1 | Z-order скачки на реактивных апдейтах | ⬆️ P2→P1 |
| **UX-8** | **UX** | **P1** | **Этап 1** | **wm.open unconditional focus при reuse** | **🆕 v2** |
| **UX-9** | **UX** | **P2** | **Этап 1** | **NodeCard anchor drift при pan/zoom** | ✅ Done |
| ARCH-1 | Arch | P2 | Этап 4 | Дуальный render path | — |
| ARCH-2 | Arch | P2 | Этап 3 | Монолитный SimulatorAppRoot | — |
| ARCH-3 | Arch | P2 | Этап 3 | Нет единого state machine | — |
| ARCH-4 | Arch | P3 | Этап 3 | Циклические watchers | — |
| ARCH-6 | Arch | P2 | Этап 2 | Множество ad-hoc EdgeDetail flags | ✅ Done |
| ARCH-7 | Arch/UX | **P1** | Этап 2 | keepAlive EdgeDetail context drift | ⬆️ P2→P1 |
| TODO-ESC | Arch/UX | P3 | Этап 1 | ESC-диспетчеризация локализована на DOM-контейнер конкретного окна (не `window`) | ✅ Done |
| PERF-2 | Perf | P3 | Этап 5 | RO → reclamp chain | ✅ Done |
| PERF-3 | Perf | P3 | Этап 3 | Множественные watchers | — |
| PERF-4 | Perf | P2 | Этап 5 | Hardcoded sizes vs content | ✅ Done |
| RACE-1 | Race | P3 | Этап 3 | Rapid node clicks | ✅ Done |
| RACE-2 | Race | P2 | Этап 1 | ESC during async | — |
| RACE-3 | Race | P3 | Этап 3 | Concurrent wm.open | — |
| RACE-4 | Race | P2 | Этап 1 | Stale epoch in runBusy | ✅ Done |

## Приложение C: Acceptance Criteria recap (из спецификаций)

### ESC Step-Back AC

| AC | Сценарий | Expected | Status |
|----|---------|----------|--------|
| AC-1 | Payment из NodeCard: ESC на picking-to | 1 ESC → close | ✅ Implemented |
| AC-2 | Payment из ActionBar: ESC step-back | picking-from←picking-to←confirm | ✅ Implemented |
| AC-3 | Payment из EdgeDetail: ESC | 2 ESC (back + close) | ✅ Implemented |
| AC-4 | Trustline из NodeCard: ESC на picking-to | 1 ESC → close | ✅ Implemented |
| AC-5 | Edit Trustline из NodeCard: ESC на editing | 2 ESC (back + close) | ✅ Implemented |
| AC-6 | FROM dropdown includes prefilled participant | Always present | ✅ Implemented |
| AC-7 | Clearing: no step-back | 1 ESC → close | ✅ Implemented |
| AC-8 | Legacy ESC stack | N/A (legacy runtime удалён) | ✅ Implemented |

### WM Acceptance Criteria

| AC | Требование | Status |
|----|-----------|--------|
| WM-1 | WM единственный source of truth | ✅ |
| WM-2 | Group limits: max 1 per group, coexistence | ✅ |
| WM-3 | ESC back-stack + form-guard | ✅ |
| WM-4 | Canvas click = hard dismiss | ✅ |
| WM-5 | Geometry: RO, reclamp, pad 12px, snap 8px | ✅ |
| WM-6 | Frameless (geometry-only) | ✅ |
| WM-7 | Unified transition | ✅ |
| WM-8 | No double header/surface | ✅ |

### Известные TODO (из кода)

| TODO | Файл | Описание | Приоритет |
|------|------|---------|-----------|
| TODO-ESC (✅ Done) | [`windowContainerContext.ts`](simulator-ui/v2/src/composables/windowManager/windowContainerContext.ts:1) | ESC dispatch локализован на DOM-container окна (provide/inject), без глобального `window` listener | P3 |
| A11y: focus-trap; focus-return | focus-return ✅ (UX-2) | Focus-trap остаётся TODO; focus-return внедрён | P2 |
| Bottom-sheet для узких экранов | — | POST-MVP | P4 |

#### TODO-ESC (Done): Локализация ESC-диспетчеризации на контейнер окна

**Статус:** ✅ Done

**Результат:**
- ESC-обработка/диспетчеризация больше не глобальная на `window`, а привязана к DOM-контейнеру конкретного окна.
- Контейнер передаётся через provide/inject (InjectionKey), чтобы избежать прокидывания через пропсы и случайных пересечений между окнами.

**Где в коде:**
- InjectionKey/context: [`windowContainerContext.ts`](simulator-ui/v2/src/composables/windowManager/windowContainerContext.ts:1)
- Provider (контейнер окна): [`WindowShell.vue`](simulator-ui/v2/src/components/WindowShell.vue:1)
- Consumer (подписка на ESC в пределах контейнера): [`useDestructiveConfirmation.ts`](simulator-ui/v2/src/composables/useDestructiveConfirmation.ts:1)
- Dispatch на topmost container (не `window`): [`SimulatorAppRoot.vue`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:1)

**Тесты:**
- Unit test: [`useDestructiveConfirmation.test.ts`](simulator-ui/v2/src/composables/useDestructiveConfirmation.test.ts:1)

---

> **Конец документа**
>
> Автор: Автоматический аудит
> Дата: 2026-03-02
> Ревизия: 2.0 (обновлено 2026-03-03 по результатам ревью)
>
> **Changelog v2.0:**
> - Добавлены разделы 8 (Decision Log) и 9 (Типология perceived jumps)
> - UX-3: fix пересмотрен — closing state вместо debounce
> - UX-4: добавлены точные ссылки на код (Step 0 / Option C / WM hard dismiss)
> - UX-7: повышен до P1, добавлены точные ссылки
> - ARCH-7: повышен до P1, добавлены ссылки на frozenLink и props
> - Добавлены UX-8 (unconditional focus при reuse) и UX-9 (NodeCard anchor drift)
> - Раздел 4: добавлены 4.7 (что хорошо защищено) и 4.8 (уточнённые риски)
> - Приложение B: обновлена матрица проблем

> **Changelog v2.1:**
> - UX-2: помечено как ✅ Done + добавлены implementation notes и ссылки на тесты
> - UX-9: помечено как ✅ Done + добавлены implementation notes и тест throttling
> - RACE-4: помечено как ✅ Done + добавлены implementation notes и тест cancel→restart

> **Changelog v2.2:**
> - PERF-4: закрыто — phase-aware `getConstraints()` для `interact-panel` (preferredHeight зависит от `phase`) + unit-тесты: [`useWindowManager.ts`](simulator-ui/v2/src/composables/windowManager/useWindowManager.ts:1), [`useWindowManager.test.ts`](simulator-ui/v2/src/composables/windowManager/useWindowManager.test.ts:1)

> **Changelog v2.3:**
> - PERF-2: закрыто — coalesce ResizeObserver emissions (≤ 1 update/16ms) + skip no-op writes в `updateMeasuredSize()`/`reclamp()` + unit-тесты: [`WindowShell.vue`](simulator-ui/v2/src/components/WindowShell.vue:1), [`useWindowManager.ts`](simulator-ui/v2/src/composables/windowManager/useWindowManager.ts:1), [`WindowShell.test.ts`](simulator-ui/v2/src/components/WindowShell.test.ts:1), [`useWindowManager.test.ts`](simulator-ui/v2/src/composables/windowManager/useWindowManager.test.ts:1)

> 
> **Changelog v2.4:**
> - ARCH-6: закрыто — EdgeDetail ref-флаги заменены на composable-state-machine [`useWmEdgeDetail()`](simulator-ui/v2/src/composables/useWmEdgeDetail.ts:1) + unit-тест transitions: [`useWmEdgeDetail.ts`](simulator-ui/v2/src/composables/useWmEdgeDetail.ts:1), [`SimulatorAppRoot.vue`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:1), [`useWmEdgeDetail.test.ts`](simulator-ui/v2/src/composables/useWmEdgeDetail.test.ts:1)
> - UX-5: закрыто — повторный dblclick на ту же ноду не вызывает [`wm.open()`](simulator-ui/v2/src/composables/windowManager/useWindowManager.ts:1) (early return по `nodeId`) + тест: [`SimulatorAppRoot.vue`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:1), [`SimulatorAppRoot.interact.test.ts`](simulator-ui/v2/src/components/SimulatorAppRoot.interact.test.ts:1)
> - RACE-1: закрыто — debounce canvas dblclick 150ms, rapid dblclicks → применяется только последняя нода + unit-тест: [`useCanvasInteractions.ts`](simulator-ui/v2/src/composables/useCanvasInteractions.ts:1), [`useCanvasInteractions.test.ts`](simulator-ui/v2/src/composables/useCanvasInteractions.test.ts:1)

> 
> **Changelog v2.5:**
> - TODO-ESC: закрыто — ESC dispatch локализован на DOM-container окна (не `window`), container пробрасывается через InjectionKey (provide/inject) + unit-тест: [`windowContainerContext.ts`](simulator-ui/v2/src/composables/windowManager/windowContainerContext.ts:1), [`WindowShell.vue`](simulator-ui/v2/src/components/WindowShell.vue:1), [`SimulatorAppRoot.vue`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:1), [`useDestructiveConfirmation.ts`](simulator-ui/v2/src/composables/useDestructiveConfirmation.ts:1), [`useDestructiveConfirmation.test.ts`](simulator-ui/v2/src/composables/useDestructiveConfirmation.test.ts:1)

> 
> **Changelog v2.6:**
> - UX-6: ✅ Done — trailing debounce **90ms** для [`wm.open()`](simulator-ui/v2/src/composables/windowManager/useWindowManager.ts:1) в режиме `reuse` для `type: 'node-card'`; применяется только последний payload; не затрагивает watcher-driven anchor follow из UX-9 (вызовы с `focus: 'never'` не дебаунсятся) + unit-тест: [`useWindowManager.ts`](simulator-ui/v2/src/composables/windowManager/useWindowManager.ts:1), [`useWindowManager.test.ts`](simulator-ui/v2/src/composables/windowManager/useWindowManager.test.ts:1)
