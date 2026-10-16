# Спецификация: Рефакторинг управления окнами Interact Mode
> Дата: 2026-02-21 | Ревизия: R3 2026-02-21 | Статус: DONE | Статус ревью реализации: DONE — все пункты закрыты, §7 реализован | Автор: code-review

---

## 1. Контекст

Interact Mode — интерактивный режим simulator-ui/v2 (Vue 3 + Composition API), где пользователь управляет платежами, trustline-ами и клирингом через визуальный граф.

Точка входа (entry-point) сборки состояния и wiring UI: [`useSimulatorApp.ts`](../../../../../simulator-ui/v2/src/composables/useSimulatorApp.ts:1) (фасад composables). Центральный механизм Interact Mode — FSM ([`useInteractMode.ts`](../../../../../simulator-ui/v2/src/composables/useInteractMode.ts:1)) с 11 фазами:

```
idle → picking-payment-from → picking-payment-to → confirm-payment
     → picking-trustline-from → picking-trustline-to → confirm-trustline-create
     → editing-trustline
     → confirm-clearing → clearing-preview → clearing-running
```

Компоненты: TopBar, ActionBar, ManualPaymentPanel, TrustlineManagementPanel, ClearingPanel, EdgeDetailPopup, NodeCardOverlay, EdgeTooltip, ErrorToast, InteractHistoryLog, SystemBalanceBar.

---

## 2. Обнаруженные проблемы

### 2.1 [HIGH] Мёртвый `v-if` в EdgeDetailPopup — popup__grid не рендерится

**Файл:** `simulator-ui/v2/src/components/EdgeDetailPopup.vue:125`

**Описание:** Секция статистики ребра (Used / Limit / Available / Status) имеет условие `v-if="phase !== 'editing-trustline'"`. Но сам попап показывается только когда `open = phase === 'editing-trustline' && !!anchor`. Условия взаимоисключаемы — блок **никогда** не рендерится пользователю.

**Текущий код:**

```html
<div v-if="open" class="ds-panel popup" :style="popupStyle">
  <!-- Заголовок и участники... -->
  
  <!-- МЁРТВЫЙ КОД: phase !== 'editing-trustline' никогда true внутри open -->
  <div v-if="phase !== 'editing-trustline'" class="popup__grid">
    <div class="ds-label">Used</div>
    <div class="ds-value ds-mono">{{ used ?? '—' }} {{ unit }}</div>
    <div class="ds-label">Limit</div>
    <div class="ds-value ds-mono">{{ limit ?? '—' }} {{ unit }}</div>
    <!-- ... -->
  </div>
</div>
```

**Требуемое исправление:** Удалить `v-if="phase !== 'editing-trustline'"` с элемента `popup__grid`. Родительский `v-if="open"` уже гарантирует нужную фазу.

**Результат:** Пользователь при клике на ребро увидит в попапе Used/Limit/Available/Status данные.

---

### 2.2 [MEDIUM] edgeDetailAnchor не сбрасывается при открытии из NodeCard

**Файл:** `simulator-ui/v2/src/components/SimulatorAppRoot.vue:411-413`

**Описание:** `edgeDetailAnchor` устанавливается ТОЛЬКО при клике на ребро и никогда не сбрасывается при переходе в `editing-trustline` другим путём (через NodeCard). Сценарий:

1. Пользователь кликнул ребро → anchor = {400, 200}
2. Закрыл редактирование
3. Открыл редактирование через NodeCard → anchor остаётся {400, 200}
4. EdgeDetailPopup появляется в старой позиции (или вообще не по месту)

**Требуемое исправление:**

```typescript
// SimulatorAppRoot.vue
function onInteractEditTrustline(fromPid: string, toPid: string) {
  setNodeCardOpen(false)
  edgeDetailAnchor.value = null  // ← ДОБАВИТЬ
  interact.mode.selectTrustline(fromPid, toPid)
}

function onInteractNewTrustline(fromPid: string) {
  setNodeCardOpen(false)
  edgeDetailAnchor.value = null  // ← ДОБАВИТЬ
  interact.mode.startTrustlineFlow()
  interact.mode.setTrustlineFromPid(fromPid)
}
```

---

### 2.3 [MEDIUM] newLimit не обновляется при смене trustline внутри editing-фазы

**Файл:** `simulator-ui/v2/src/components/TrustlineManagementPanel.vue:39-53`

**Описание:** Watch на `props.phase` заполняет `newLimit` только при *переходе* в `editing-trustline`. Когда пользователь меняет trustline через dropdown «Existing» (selectTrustline), фаза остаётся `editing-trustline`, watch не триггерится, поле «New limit» показывает значение предыдущего trustline.

**Требуемое исправление:** Добавить дополнительный watcher:

```typescript
watch(
  () => `${String(props.state.fromPid ?? '')}→${String(props.state.toPid ?? '')}`,
  () => {
    if (props.phase === 'editing-trustline') {
      const cur = props.currentLimit
      newLimit.value = (cur != null && String(cur).trim()) ? String(cur) : ''
    }
  },
)
```

---

### 2.4 [MEDIUM] Double-click в picking-фазе открывает NodeCardOverlay одновременно с панелью

**Файлы:** `simulator-ui/v2/src/composables/useSimulatorApp.ts:1323`, `simulator-ui/v2/src/composables/useCanvasInteractions.ts:92-96`

**Описание:** При double-click на узел `onCanvasDblClick` безусловно вызывает `setNodeCardOpen(true)`. В picking-фазах single-click продвигает FSM (выбор from/to). Результат: ManualPaymentPanel (или TrustlinePanel) остаётся открытой И одновременно открывается NodeCardOverlay — два диалога на экране.

**Требуемое исправление:** В wiring double-click добавить guard:

```typescript
// В useSimulatorApp.ts или useAppCanvasInteractionsWiring
onCanvasDblClick: (ev: MouseEvent) => {
  wakeUp()
  if (isInteractPickingPhase.value) {
    // В picking-фазах double-click = обычный single-click (выбор узла)
    canvasInteractions.onCanvasClick(ev)
    return
  }
  canvasInteractions.onCanvasDblClick(ev)
}
```

Где `isInteractPickingPhase` — computed:

```typescript
const isInteractPickingPhase = computed(() => {
  const p = interact?.mode?.state?.phase
  return p?.startsWith('picking-') ?? false
})
```

---

### 2.5 [MEDIUM] EdgeDetailPopup clamping не учитывает ширину попапа

**Файл:** `simulator-ui/v2/src/components/EdgeDetailPopup.vue:50-52`

**Описание:** Clamping по X: `clamp(anchor.x + 12, 10, w - 10)`. При клике у правого края viewport попап (min-width: 260px) начинается в 10px от правого края и уезжает за границу на 250px.

**Требуемое исправление:**

```typescript
const MIN_POPUP_W = 260  // = min-width в CSS
const MIN_POPUP_H = 140  // приблизительная минимальная высота
const pad = 10
const x = clamp(props.anchor.x + 12, pad, w - MIN_POPUP_W - pad)
const y = clamp(props.anchor.y + 12, pad, h - MIN_POPUP_H - pad)
```

---

### 2.6 [LOW] updateValid использует props.used вместо effectiveUsed

**Файл:** `simulator-ui/v2/src/components/TrustlineManagementPanel.vue:55-77`

**Описание:** Валидатор `newLimit >= usedNum` берёт `props.used` (из снапшота графа), а UI показывает `effectiveUsed` (из fetched trustlines — более свежие данные). Рассинхрон: пользователь видит одно значение Used, а валидация срабатывает по другому.

**Требуемое исправление:**

```typescript
const usedNum = computed(() => {
  const v = Number(effectiveUsed.value ?? 0)
  return Number.isFinite(v) ? v : 0
})
```

---

### 2.7 [LOW] ActionBar не блокирует переключение в активной фазе

**Файл:** `simulator-ui/v2/src/components/ActionBar.vue:22-31`

**Описание:** При активном flow (phase != idle) переключение между flow через ActionBar должно быть заблокировано, иначе пользователь теряет введённые данные без предупреждения (например, заполнил форму платежа и нажал «Run Clearing»).

**Принятый вариант FIX-7 (вариант B — строгий):** Пока текущий flow не отменён (Cancel/ESC), ActionBar блокирует запуск любого другого flow:

- кнопки в ActionBar disabled,
- отображается явная причина блокировки (title + hint «Cancel current action first»),
- обработчики кликов дополнительно защищены guard-ом.

---

### 2.8 [LOW] popup__actions без flex-wrap и gap

**Файл:** `simulator-ui/v2/src/components/EdgeDetailPopup.vue:192-196`

**Описание:** При `closeArmed=true` появляются 4 кнопки (~350px). На мобильных (min-width: 260px) кнопки накладываются друг на друга.

**Требуемое исправление:**

```css
.popup__actions {
  display: flex;
  flex-wrap: wrap;    /* добавить */
  gap: 6px;           /* добавить */
  justify-content: flex-end;
  margin-top: 10px;
}
```

---

### 2.9 [HIGH] EdgeDetailPopup: edgeDetailAnchor не сбрасывается при cancel/ESC

**Файл:** `simulator-ui/v2/src/composables/useSimulatorApp.ts:861`, `useInteractMode.ts:cancel()`

**Описание:** `edgeDetailAnchor` (ref в `useSimulatorApp.ts:861`) не сбрасывается в `null` ни при `cancel()`, ни при `resetToIdle()` в FSM. Это расширение бага §2.2 — стейл anchor может «всплыть» не только при открытии из NodeCard, но и в любом последующем `editing-trustline`, открытом не через клик на ребро.

**Сценарий воспроизведения:**
1. Клик на ребро → anchor = {400, 200}, EdgeDetailPopup рендерится
2. ESC → `cancel()` → phase = idle, **но** `edgeDetailAnchor` = {400, 200}
3. Через NodeCard: «Edit trustline» → `onInteractEditTrustline` → phase = editing-trustline
4. `open = phase === 'editing-trustline' && !!anchor` → **true** (стейл anchor!)
5. Попап рендерится в позиции старого ребра, а не рядом с текущим

**Требуемое исправление:** Помимо §2.2, добавить сброс при cancel — или (лучше) реализовать §3.1 (централизация anchor в FSM).

**Workaround (минимальный):** В `SimulatorAppRoot.vue` добавить watch:
```typescript
watch(interactPhase, (p) => {
  if (p === 'idle') edgeDetailAnchor.value = null
})
```

---

### 2.10 [MEDIUM] ErrorToast и InteractHistoryLog визуально перекрываются

**Файлы:** `ErrorToast.vue` (CSS: `bottom: 68px; left: 50%; z-index: 200`), `SimulatorAppRoot.vue` (inline: `right: 12px; bottom: 68px`)

**Описание:** При одновременном наличии ошибки и записей истории — оба элемента позиционируются в одной полосе `bottom: 68px`. ErrorToast центрирован (`left: 50%`, max-width 480px), history справа. На viewport < ~1000px тост перекрывает history, и наоборот — history (pointer-events: none) не мешает клику, но тост закрывает текст.

**Требуемое исправление:** Сдвинуть InteractHistoryLog выше:
```html
<!-- SimulatorAppRoot.vue -->
<div
  v-if="isInteractUi && interact.mode.history.length > 0"
  class="ds-ov-bottom"
  style="right: 12px; left: auto; bottom: 120px; ..."
>
```
Или привязать ErrorToast к `bottom: 120px` и дать history `bottom: 68px` (ближе к BottomBar).

---

### 2.11 [MEDIUM] ManualPaymentPanel и TrustlineManagementPanel: одновременное позиционирование top-right

**Файлы:** `designSystem.overlays.css` (`.ds-ov-panel { right: 12px; top: 110px }`), `SimulatorAppRoot.vue`

**Описание:** Оба панели используют одинаковый CSS-класс `.ds-ov-panel` (position: absolute; right: 12px; top: 110px). FSM гарантирует, что обе панели НЕ открыты одновременно — но при быстрой смене фаз (ActionBar click) анимация `<Transition name="panel-slide">` создаёт момент, когда выходящая панель ещё не ушла, а входящая уже появляется. Обе рендерятся в одном месте ~0.2с.

**Контекст:** Сейчас это косметическая проблема, т.к. обычный user flow: cancel → idle → startNewFlow. Но прямое переключение (из confirm-payment → startTrustlineFlow) через ActionBar (§2.7 позволяет) создаёт визуальное наложение.

**Требуемое исправление (вариант А — quick):** Добавить `mode="out-in"` в Transition:
```html
<Transition name="panel-slide" mode="out-in">
```
Недостаток: Vue `mode="out-in"` требует, чтобы оба элемента имели единый parent Transition, что невозможно для двух разных компонентов.

**Требуемое исправление (вариант Б — правильный):** Обернуть все три панели в единый «слот» с computed-ключом:
```html
<component :is="activePanelComponent" :key="activePanelKey" ... />
```
Где `activePanelComponent` возвращает один из трёх компонентов по фазе.

**Требуемое исправление (вариант В — минимальный):** Добавить `position: relative` и `z-index` дифференциацию: новая панель выше старой.

---

### 2.12 [MEDIUM] ClearingPanel: кнопка «Close» доступна во время clearing-preview/running

**Файл:** `simulator-ui/v2/src/components/ClearingPanel.vue:85-87`

**Описание:** В фазах `clearing-preview` и `clearing-running` кнопка Close (`cancel`) отключена через `busyUi = busy || isRunning`. Однако в `clearing-preview` `isRunning = false` и `busy = true` (runBusy в процессе), поэтому кнопка корректно заблокирована. НО: если clearing endpoint ответил быстро и busy снялся до `CLEARING_PREVIEW_DWELL_MS` (800ms) — кнопка станет кликабельна, хотя пользователь ещё не успел прочитать результат.

**Точнее:** В `useInteractMode.ts:confirmClearing`, после `await opts.actions.runClearing()` busy продолжает удерживаться (`runBusy` wrapper), но `setTimeout(r, CLEARING_PREVIEW_DWELL_MS)` — это await внутри runBusy. Значит busy = true пока dwell не пройдёт. **Проблема не воспроизводится** — анализ ошибочный. 

**Статус:** ~~MEDIUM~~ → **НЕ ПОДТВЕРЖДЕНО** (busy корректно удерживается). Пометка для ясности.

---

### 2.13 [LOW] TrustlineManagementPanel: dropdown «Existing» в picking-from показывает все TL без фильтрации по from

**Файл:** `simulator-ui/v2/src/components/TrustlineManagementPanel.vue:173-180`

**Описание:** `trustlinesForFrom` computed: если `state.fromPid` ещё null (фаза `picking-trustline-from`), возвращает **все** trustlines. Dropdown «Existing» доступен и показывает полный список. Пользователь может кликнуть любой TL, что перепрыгнет в `editing-trustline`, минуя picking-шаги. Это shortcut, но:
- Неочевидно для нового пользователя
- Нарушает ожидаемый flow: «сначала выбрать From, потом To»
- В фазе `isPickFrom` заголовок панели «Trustline» не намекает на наличие shortcut

**Связано с:** §3.5 (ограничить видимость dropdown).

---

### 2.14 [LOW] NodeCardOverlay: interact actions не проверяют busy-статус

**Файл:** `simulator-ui/v2/src/components/NodeCardOverlay.vue:143-155`

**Описание:** Кнопки «💸 Send Payment» и «＋ New Trustline» в NodeCard не отключаются при `busy = true`. Если пользователь кликнет «Send Payment» пока running trustline update — `startPaymentFlow()` проверяет `if (busyRef.value) return`, поэтому клик будет проигнорирован. Проблема чисто UX: кнопка выглядит кликабельной, но ничего не делает.

**Требуемое исправление:**
```html
<button
  class="ds-btn ds-btn--primary ds-btn--sm"
  type="button"
  :disabled="interactBusy"
  @click="onInteractSendPayment?.(node.id)"
>
```
Не использовать loading-флаг для disable кнопок: для UX нужен общий busy-флаг. Лучше добавить prop `interactBusy`:
```typescript
:interact-busy="isInteractUi ? interact.mode.busy.value : undefined"
```

---

### 2.15 [CRITICAL] z-index конфликт: world labels / floating layers перекрывают окна и панели

**Файлы:** [`simulator-ui/v2/src/App.css`](simulator-ui/v2/src/App.css:89), [`simulator-ui/v2/src/ui-kit/designSystem.overlays.css`](simulator-ui/v2/src/ui-kit/designSystem.overlays.css:217)

**Описание:** Слои «world labels / floating layers» имеют z-index выше, чем у interact-окон (панели/попапы). В результате при некоторых сценах/масштабах/переключениях label-слои оказываются поверх окон: окно визуально присутствует, но читаемость и кликабельность ухудшаются (окно частично или полностью закрыто текстовыми лейблами/слоями).

**Требуемое исправление:**
1) Ввести единую шкалу z-index (см. §3.9) и зафиксировать правило: **окна/панели должны быть выше world-labels**, кроме отдельно оговорённых исключений.
2) Привести текущие значения z-index в указанных стилях к этой шкале (минимально: понизить labels/floating layers ниже окон, либо поднять окна выше labels — в зависимости от принятого правила).

**Результат:** Любое interact-окно всегда визуально и интерактивно «поверх» world labels.

---

### 2.16 [HIGH] EdgeTooltip clamp не учитывает ширину тултипа

**Файл:** [`simulator-ui/v2/src/composables/useEdgeTooltip.ts`](simulator-ui/v2/src/composables/useEdgeTooltip.ts:41)

**Описание:** Позиционирование EdgeTooltip использует clamping по viewport, но учитывает только точку якоря/паддинги. Ширина тултипа не участвует в вычислении, из-за чего при наведении на ребро у правого края тултип уезжает за границы экрана.

**Требуемое исправление:** Считать clamping по X/Y с учётом фактических размеров тултипа (или гарантированных min/max размеров). Варианты:
- измерять `tooltipEl.getBoundingClientRect()` после рендера и подставлять `w - tooltipWidth - pad`;
- изменить CSS на центрирование (`transform: translateX(-50%)`) и clamp относительно половины ширины.

**Результат:** EdgeTooltip всегда полностью видим в пределах viewport.

---

### 2.17 [HIGH] Несовпадение систем координат: anchor считается в host rect, а clamp — в viewport

**Файлы:** anchor: [`simulator-ui/v2/src/composables/useSimulatorApp.ts`](simulator-ui/v2/src/composables/useSimulatorApp.ts:1331), clamp: [`simulator-ui/v2/src/components/EdgeDetailPopup.vue`](simulator-ui/v2/src/components/EdgeDetailPopup.vue:41)

**Описание:** Якорь для popup вычисляется в одной системе координат (относительно host/container прямоугольника), а clamping выполняется в другой (viewport `innerWidth/innerHeight`). При наличии отступов контейнера, скролла, трансформаций или изменения layout это даёт систематическое смещение: popup может «улетать» от ребра и/или неправильно clamp-иться.

**Требуемое исправление:** Привести anchor и clamping к **одной** системе координат:
- либо хранить anchor в viewport-координатах (например, clientX/clientY),
- либо clamp-ить относительно размеров и origin того же host rect, в котором вычислен anchor.

**Результат:** EdgeDetailPopup стабильно появляется рядом с выбранным ребром и корректно удерживается внутри видимой области.

---

### 2.18 [MEDIUM] Нормализация `''` vs `null/undefined` ломает отображение «—»

**Файлы:** snapshot mapping: [`simulator-ui/v2/src/composables/useInteractMode.ts`](simulator-ui/v2/src/composables/useInteractMode.ts:266); рендер через `??`: [`simulator-ui/v2/src/components/EdgeDetailPopup.vue`](simulator-ui/v2/src/components/EdgeDetailPopup.vue:125), [`simulator-ui/v2/src/components/TrustlineManagementPanel.vue`](simulator-ui/v2/src/components/TrustlineManagementPanel.vue:280)

**Описание:** Значения, которые должны отсутствовать, иногда приходят как пустая строка `''`, а иногда как `null/undefined`. В шаблонах используется nullish-coalescing (`value ?? '—'`), который **не** подставляет «—» для `''`. В итоге пользователь видит пустое значение вместо ожидаемого «—».

**Требуемое исправление:** Зафиксировать единое правило нормализации (см. §3.11):
- на уровне маппинга данных приводить `''` (и строки из пробелов) к `null`,
- и/или на уровне отображения использовать проверку на пустую строку (например, `value ? value : '—'` / `String(value).trim()` вместо `??`).

**Результат:** Плейсхолдер «—» отображается предсказуемо для всех отсутствующих значений.

---

### 2.19 [MEDIUM] NodeCardOverlay не гарантированно закрывается при старте flow из ActionBar

**Файл:** [`simulator-ui/v2/src/components/SimulatorAppRoot.vue`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:523)

**Описание:** Сценарий «NodeCard открыт → пользователь запускает новый flow через ActionBar (start*Flow)» не гарантирует закрытие NodeCardOverlay. В результате возможно наложение NodeCardOverlay и панели текущего flow: формально это два независимых оверлея без общего правила взаимоисключения.

**Требуемое исправление:** Ввести правило overlay stack (см. §3.10) и обеспечить, что `startPaymentFlow`/`startTrustlineFlow`/`startClearingFlow` закрывают NodeCardOverlay (и другие конкурирующие оверлеи), либо переводят UI в строго определённое состояние «одно окно».

**Результат:** При старте любого flow UI приходит к детерминированному набору видимых окон.

---

### 2.20 [MEDIUM] Разделение busy vs loading: `interactBusy` и `trustlinesLoading`

**Файл:** [`simulator-ui/v2/src/components/SimulatorAppRoot.vue`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:609)

**Описание:** До рефакторинга использовался единый флаг, который смешивал два разных смысла: общий «UI занят» и узкое состояние «идёт загрузка trustlines». Это затрудняло wiring, чтение кода и корректное отключение кнопок (см. также §2.14).

**Требуемое исправление:** Разделить понятия (см. §3.10/§3.11):
- `interactBusy` (общий busy FSM/операций),
- `trustlinesLoading` (загрузка/рефреш trustlines).
И переименовать/перевести использования соответственно.

**Результат:** Код явно выражает причину disable UI и снижает риск регрессий.

---

### 2.21 [LOW] UX/tech-debt: арбитраж ESC и приоритет закрытия оверлеев без явного стека

**Файл:** [`simulator-ui/v2/src/components/SimulatorAppRoot.vue`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:208)

**Описание:** Глобальный обработчик ESC закрывает элементы UI по набору условий, но без явной модели «стека оверлеев». При усложнении интерфейса (несколько типов окон: NodeCard, popup, панели, тосты, дев-оверлей) появляется риск неконсистентного порядка закрытия и «проброса» ESC не туда.

**Требуемое исправление:** Ввести явный приоритет/стек оверлеев (см. §3.10): определить порядок закрытия и единый механизм выбора «что закрыть первым».

**Результат:** ESC работает предсказуемо, в одном и том же порядке для всех окон.

---

## 3. Рефакторинг

### 3.1 Централизовать edgeDetailAnchor в FSM-состояние

**Файлы затронуты:** `useInteractMode.ts`, `SimulatorAppRoot.vue`, `EdgeDetailPopup.vue`

**Описание:** Сейчас `edgeDetailAnchor` живёт в `useSimulatorApp.ts` отдельно от `InteractState`. Он семантически привязан к фазе `editing-trustline`.

**Предлагаемое изменение:**

1. Добавить в `InteractState`:

```typescript
export type InteractState = {
  phase: InteractPhase
  fromPid: string | null
  toPid: string | null
  selectedEdgeKey: string | null
  error: string | null
  lastClearing: SimulatorActionClearingRealResponse | null
  edgeAnchor: { x: number; y: number } | null  // ← ДОБАВИТЬ
}
```

2. В `selectEdge(edgeKey, anchor?)` — устанавливать:

```typescript
function selectEdge(edgeKey: string, anchor?: { x: number; y: number }) {
  state.selectedEdgeKey = edgeKey
  state.edgeAnchor = anchor ?? null
  // ... rest
}
```

3. В `cancel()` / `resetToIdle()` — автоматически сбрасывать:

```typescript
function resetToIdle() {
  state.phase = 'idle'
  state.fromPid = null
  state.toPid = null
  state.selectedEdgeKey = null
  state.edgeAnchor = null  // ← ДОБАВИТЬ
  state.error = null
}
```

4. Убрать `edgeDetailAnchor` из `SimulatorAppRoot.vue`.

**Выигрыш:** Устраняет баг 2.2 (stale anchor), упрощает отладку (всё состояние в одном месте), устраняет coupling между SimulatorAppRoot и EdgeDetailPopup.

---

### 3.2 Разделить useInteractMode.ts на слои

**Файл:** `simulator-ui/v2/src/composables/useInteractMode.ts` (791 строк)

**Описание:** Файл содержит FSM-логику, кэширование данных (participants/trustlines с TTL), бизнес-логику (runBusy, epoch) и history log.

**Предлагаемая декомпозиция:**

```
useInteractMode.ts (791 строк)
   ├── useInteractFSM.ts        — чистые переходы состояний (~200 строк)
   ├── useInteractDataCache.ts  — participants + trustlines с freshness tracking (~250 строк)
   ├── useInteractHistory.ts    — history log с pushHistory/entries (~100 строк)
   └── useInteractMode.ts       — композиция: FSM + cache + history + runBusy (~250 строк)
```

**Приоритет:** LOW — это рефакторинг, не исправление бага. Делать после исправления issues.

---

### 3.3 Guard для double-click в picking-фазах (реализация)

Полная реализация описана в п. 2.4. Дополнительно: добавить единый computed `isPickingPhase` в `useInteractMode.ts`:

```typescript
const isPickingPhase = computed(() => state.phase.startsWith('picking-'))
```

И экспортировать его для использования в `useSimulatorApp.ts` и `useCanvasInteractions.ts`.

---

### 3.4 Единый источник данных для валидации

Описано в п. 2.6. Дополнительно: рассмотреть создание computed-свойства `effectiveData` в `TrustlineManagementPanel.vue`, которое объединяет данные из fetched trustlines с fallback на snapshot props. Использовать его И для отображения, И для валидации.

---

### 3.5 Ограничить видимость dropdown «Existing» в TrustlineManagementPanel

**Файл:** `simulator-ui/v2/src/components/TrustlineManagementPanel.vue:258-275`

**Описание:** Dropdown «Existing» показывается всегда когда есть trustlines, включая фазу `isPickFrom`. Это даёт shortcut (перепрыгнуть в editing) но неочевидно для пользователя.

**Предлагаемое:** Показывать dropdown «Existing» только в фазах `isCreate || isEdit`:

```html
<div v-if="(isCreate || isEdit) && trustlinesSorted.length > 0" class="ds-field">
  <label for="tl-pick">Existing</label>
  <select id="tl-pick" ...>
```

---

### 3.6 Единый слот для interact-панелей (устранение visual overlap)

**Файлы:** `SimulatorAppRoot.vue`

**Описание:** Три interact-панели (ManualPaymentPanel, TrustlineManagementPanel, ClearingPanel) рендерятся как отдельные `<Transition>` блоки в одной позиции (right: 12px, top: 110px). При быстрой смене фаз — кратковременное наложение (§2.11).

**Предлагаемое решение:** Заменить три отдельных блока на единый computed-driven слот:

```html
<Transition name="panel-slide" mode="out-in">
  <ManualPaymentPanel v-if="activePanelType === 'payment'" key="payment" ... />
  <TrustlineManagementPanel v-else-if="activePanelType === 'trustline'" key="trustline" ... />
  <ClearingPanel v-else-if="activePanelType === 'clearing'" key="clearing" ... />
</Transition>
```

Где:
```typescript
const activePanelType = computed<'payment' | 'trustline' | 'clearing' | null>(() => {
  const p = interactPhase.value
  if (p.includes('payment')) return 'payment'
  if (p.includes('trustline')) return 'trustline'
  if (p.includes('clearing')) return 'clearing'
  return null
})
```

**Выигрыш:** Vue `mode="out-in"` гарантирует, что выходящая панель полностью уйдёт перед появлением новой. Нет наложений.

---

### 3.7 Разнести z-index overlay-слоёв ErrorToast vs. HistoryLog

**Файлы:** `ErrorToast.vue`, `SimulatorAppRoot.vue`

**Описание:** ErrorToast (`z-index: 200`) и HistoryLog (контейнер `ds-ov-bottom`, нет явного z-index) перекрываются при `bottom: 68px`. 

**Предлагаемое:** Вынести ErrorToast и HistoryLog в стабильные, неконфликтующие позиции:
- HistoryLog: `bottom: 130px; right: 12px` (над BottomBar, ниже панелей)
- ErrorToast: `bottom: 68px; left: 50%` (текущее) — ОК, но рассмотреть `top: auto; bottom: calc(100% - ...)` привязку к BottomBar.

---

### 3.8 Добавить prop `interactBusy` в NodeCardOverlay

**Файлы:** `NodeCardOverlay.vue`, `SimulatorAppRoot.vue`

**Описание:** Interact-кнопки в NodeCard (Send Payment, New Trustline) не отключаются при `busy=true` (§2.14).

**Предлагаемое:** 
1. Добавить prop `interactBusy?: boolean` в NodeCardOverlay
2. Пробросить из SimulatorAppRoot: `:interact-busy="isInteractUi ? interact.mode.busy.value : false"`
3. Использовать `:disabled="interactBusy"` на кнопках

---

### 3.9 Ввести z-index tokens и единую шкалу для всех overlay-слоёв

**Файлы затронуты:** CSS design-system + App-level CSS (см. §2.15)

**Описание:** Сейчас z-index задаётся точечно и локально (частично в design system, частично в app-стилях), из-за чего появляются конфликты (напр. world labels поверх окон — §2.15). Нужна единая модель уровней.

**Предлагаемое:**
1) Ввести токены (CSS custom properties или дизайн-токены) для уровней, например:

```
--z-base: 0
--z-bar: 30
--z-topbar: 40
--z-panel: 70
--z-tooltip: 80
--z-dev: 90
--z-alert: 200
```

2) Привязать все overlay-классы и app-specific слои (включая world labels) к этим токенам.

**Выигрыш:** Детерминированный порядок перекрытия; локальные правки не ломают другие окна.

---

### 3.10 Overlay stack: правило одно окно или явный порядок сосуществования

**Файлы затронуты:** `SimulatorAppRoot.vue` wiring, компоненты overlay-окон

**Описание:** Сейчас часть окон взаимоисключается через FSM, часть — через локальные `setOpen(false)` в обработчиках, часть — вообще не имеет правила (см. §2.19, §2.21). Нужно формализовать поведение.

**Предлагаемое:** выбрать и зафиксировать один из подходов:

1) **Правило одно окно**: при старте любого flow/открытии popup закрываются все конкурирующие оверлеи (NodeCard, другие popups, панели), остаётся один «активный» overlay-контекст.
2) **Явный стек**: допускаем несколько оверлеев, но вводим порядок: кто над кем, кто закрывается по ESC/Cancel первым, какие пары допустимы.

**Выигрыш:** Устраняет недетерминированные наложения (особенно ActionBar → startFlow при открытом NodeCard).

---

### 3.11 Нормализация значений: единый контракт для отсутствующих данных

**Описание:** UI использует плейсхолдер «—», но данные приходят как `null/undefined` и как `''`. Без единого контракта возникают «пустые» значения в UI (§2.18).

**Предлагаемое:**
1) Ввести утилиту/правило: `emptyStringToNull` (или аналог) для всех значений, которые могут отсутствовать.
2) Зафиксировать, что **пустая строка считается отсутствием** (если нет отдельного значения `0`).
3) На уровне шаблонов использовать единый helper для отображения (например `renderValueOrDash`).

**Выигрыш:** Консистентный UI, меньше разрозненных проверок в шаблонах.

---

### 3.12 Унифицировать clamp-логику для popup и tooltip (ширина/высота/координаты)

**Описание:** Проблемы clamping проявляются как в popup (§2.5, §2.17), так и в tooltip (§2.16). Нужен единый подход к:
- системе координат (viewport vs host rect),
- учёту размеров элемента,
- паддингам и безопасным зонам.

**Предлагаемое:** Выделить общий helper/composable (или общую функцию) для позиционирования overlay-элементов, который принимает:
- anchor,
- bounds (viewport или host rect),
- размеры overlay (из измерения или min-size),
- padding.

**Выигрыш:** Одно место для логики clamp; меньше рассинхронов между tooltip и popup.

---

## 4. TODO Checklist

### Приоритет: CRITICAL (до релиза)

- [x] **REF-1:** Архитектурно: централизация anchor в FSM-состояние (`edgeAnchor` в `InteractState`) (§3.1) — *системно устраняет FIX-2 и FIX-9*
- [x] **FIX-14:** z-index: world labels / floating layers не должны перекрывать окна/панели (§2.15)
- [x] **FIX-16:** Привести anchor и clamp к одной системе координат (host rect vs viewport) для EdgeDetailPopup (§2.17)
- [x] **FIX-1:** EdgeDetailPopup — удалить мёртвый `v-if` с `popup__grid` (§2.1)

### Приоритет: HIGH (в ближайшем спринте)

- [x] **FIX-9:** (Если REF-1 не сделан) edgeDetailAnchor — сбрасывать при cancel/ESC/idle (§2.9)
- [x] **FIX-2:** (Если REF-1 не сделан) SimulatorAppRoot — сбросить `edgeDetailAnchor` при открытии из NodeCard (§2.2)
- [x] **FIX-5:** EdgeDetailPopup — учесть ширину попапа в clamping (§2.5)
- [x] **FIX-15:** EdgeTooltip — clamp с учётом ширины/размера тултипа (§2.16)
- [x] **FIX-4:** useSimulatorApp — guard double-click в picking-фазах (§2.4)
- [x] **FIX-3:** TrustlineManagementPanel — watcher на `from+to` для обновления `newLimit` (§2.3)
- [x] **FIX-18:** Закрывать NodeCardOverlay при старте flow из ActionBar; формализовать overlay stack (§2.19, §3.10)
- [x] **NEW-1:** useInteractMode — добавить вызов refreshParticipants() + refreshTrustlines() в selectTrustline() (§6.1/NEW-1)

### Приоритет: MEDIUM (backlog)

- [x] **FIX-6:** TrustlineManagementPanel — использовать `effectiveUsed` для валидации (§2.6)
- [x] **FIX-7:** ActionBar — строгая блокировка переключения flow до Cancel/ESC (§2.7)
- [x] **FIX-8:** EdgeDetailPopup — `flex-wrap` + `gap` для `popup__actions` (§2.8)
- [x] **FIX-10:** Разнести ErrorToast и InteractHistoryLog по вертикали (§2.10)
- [x] **FIX-11:** Панели: устранить visual overlap при быстрой смене фаз (§2.11)
- [x] **FIX-17:** Нормализация `''` vs `null/undefined` для корректного отображения «—» (§2.18)
- [x] **FIX-19:** Разделить общий busy-статус и загрузку trustlines: `interactBusy` и `trustlinesLoading` (§2.20)

### Приоритет: LOW (polish / UX)

- [x] **FIX-12:** TrustlineManagementPanel — ограничить dropdown «Existing» в picking-from (§2.13)
- [x] **FIX-13:** NodeCardOverlay — добавить `interactBusy` prop для disable кнопок (§2.14)
- [x] **FIX-20:** ESC: формализовать приоритет/стек закрытия оверлеев (UX/tech-debt) (§2.21, §3.10)
- [x] **NEW-2:** Мигрировать hardcoded z-index в компонентах на CSS-токены --ds-z-* (§6.1/NEW-2) — остался `TopBar.vue:489` Admin dropdown
- [x] **NEW-3:** SimulatorAppRoot — использовать backend-fetched trustlines как источник для interactSelectedLink, с fallback на snapshot (§6.1/NEW-3)

### Приоритет: LOW (рефакторинг)

- [x] **REF-2:** Разделить `useInteractMode.ts` на слои: FSM / DataCache / History (§3.2)
- [x] **REF-3:** Экспортировать `isPickingPhase` computed из `useInteractMode.ts` (§3.3)
- [x] **REF-4:** Единый источник данных `effectiveData` для валидации и отображения (§3.4)
- [x] **REF-5:** Ограничить видимость dropdown «Existing» фазами `isCreate || isEdit` (§3.5)
- [x] **REF-6:** Единый `<Transition mode="out-in">` слот для трёх interact-панелей (§3.6) — *устраняет FIX-11*
- [x] **REF-7:** Разнести z-index overlay-слоёв ErrorToast / HistoryLog (§3.7) — *устраняет FIX-10*
- [x] **REF-8:** Добавить prop `interactBusy` в NodeCardOverlay (§3.8) — *устраняет FIX-13*
- [x] **REF-9:** Ввести z-index tokens и единую шкалу для overlay-слоёв (§3.9) — *устраняет FIX-14*
- [x] **REF-10:** Формализовать overlay stack (одно окно или явный порядок) (§3.10) — *устраняет FIX-18, поддерживает FIX-20*
- [x] **REF-11:** Единая нормализация отсутствующих значений (§3.11) — *устраняет FIX-17*
- [x] **REF-12:** Общий helper для clamp позиционирования popup/tooltip (§3.12) — *поддерживает FIX-5, FIX-15, FIX-16*

---

## 6. Статус реализации

| ID | Приоритет | Статус | Файл/строка |
|---|---|---|---|
| FIX-1 | CRITICAL | ✅ РЕАЛИЗОВАНО | EdgeDetailPopup.vue:123 |
| FIX-2 | HIGH | ✅ РЕАЛИЗОВАНО (через REF-1) | useInteractMode.ts:580 |
| FIX-3 | HIGH | ✅ РЕАЛИЗОВАНО | TrustlineManagementPanel.vue:59 |
| FIX-4 | HIGH | ✅ РЕАЛИЗОВАНО | useSimulatorApp.ts:1343 |
| FIX-5 | HIGH | ✅ РЕАЛИЗОВАНО | overlayPosition.ts |
| FIX-6 | MEDIUM | ✅ РЕАЛИЗОВАНО | TrustlineManagementPanel.vue:88 |
| FIX-7 | MEDIUM | ✅ РЕАЛИЗОВАНО (вариант B — строгая блокировка) | ActionBar.vue |
| FIX-8 | LOW | ✅ РЕАЛИЗОВАНО | EdgeDetailPopup.vue:192 |
| FIX-9 | HIGH | ✅ РЕАЛИЗОВАНО (через REF-1) | useInteractMode.ts:258 |
| FIX-10 | MEDIUM | ✅ РЕАЛИЗОВАНО | SimulatorAppRoot.vue:701 |
| FIX-11 | MEDIUM | ✅ РЕАЛИЗОВАНО (через REF-6) | SimulatorAppRoot.vue:543 |
| FIX-12 | LOW | ✅ РЕАЛИЗОВАНО | TrustlineManagementPanel.vue:279 |
| FIX-13 | LOW | ✅ РЕАЛИЗОВАНО | NodeCardOverlay.vue:32 |
| FIX-14 | CRITICAL | ✅ РЕАЛИЗОВАНО | App.css:22 — --ds-z-world-labels: 20 |
| FIX-15 | HIGH | ✅ РЕАЛИЗОВАНО | useEdgeTooltip.ts:56 |
| FIX-16 | CRITICAL | ✅ РЕАЛИЗОВАНО | overlayPosition.ts:22 |
| FIX-17 | MEDIUM | ✅ РЕАЛИЗОВАНО | valueFormat.ts:11 |
| FIX-18 | HIGH | ✅ РЕАЛИЗОВАНО | SimulatorAppRoot.vue:432 |
| FIX-19 | MEDIUM | ✅ РЕАЛИЗОВАНО | SimulatorAppRoot.vue:621 |
| FIX-20 | LOW | ✅ РЕАЛИЗОВАНО | `escOverlayStack.ts:25`, `SimulatorAppRoot.vue:214` |
| REF-1 | CRITICAL | ✅ РЕАЛИЗОВАНО | useInteractMode.ts:34, EdgAnchor в InteractState |
| REF-2 | LOW | ✅ РЕАЛИЗОВАНО | DataCache ✓, History ✓, FSM ✓ — `useInteractFSM.ts` (341 строка, чистые FSM-переходы) |
| REF-3 | LOW | ✅ РЕАЛИЗОВАНО | useInteractMode.ts:103 |
| REF-4 | LOW | ✅ РЕАЛИЗОВАНО | TrustlineManagementPanel.vue:76 |
| REF-5 | LOW | ✅ РЕАЛИЗОВАНО | см. FIX-12 |
| REF-6 | LOW | ✅ РЕАЛИЗОВАНО | SimulatorAppRoot.vue:543 |
| REF-7 | LOW | ✅ РЕАЛИЗОВАНО | см. FIX-10 |
| REF-8 | LOW | ✅ РЕАЛИЗОВАНО | NodeCardOverlay.vue:32 |
| REF-9 | LOW | ✅ РЕАЛИЗОВАНО | App.css:17 + designSystem.overlays.css:24 |
| REF-10 | LOW | ✅ РЕАЛИЗОВАНО | `escOverlayStack.ts:25`, `SimulatorAppRoot.vue:214` |
| REF-11 | LOW | ✅ РЕАЛИЗОВАНО | valueFormat.ts + emptyToNull/renderOrDash |
| REF-12 | LOW | ✅ РЕАЛИЗОВАНО | utils/overlayPosition.ts:11 |
| NEW-1 | HIGH | ✅ РЕАЛИЗОВАНО | `useInteractMode.ts:362` |
| NEW-2 | LOW | ✅ РЕАЛИЗОВАНО | TopBar.vue:489 |
| NEW-3 | LOW | ✅ РЕАЛИЗОВАНО | `SimulatorAppRoot.vue:255` |
| §7.1 | MEDIUM | ✅ РЕАЛИЗОВАНО | useEdgeTooltip.ts:43 + test:34 |
| §7.2 | LOW | ✅ РЕАЛИЗОВАНО | useInteractFSM.ts:31,:105 |
| §7.3 | LOW | ✅ РЕАЛИЗОВАНО | NodeCardOverlay.vue:173 |
| §7.4 | LOW | ✅ РЕАЛИЗОВАНО | TrustlineManagementPanel.vue:271,:291 |
| §7.5 | LOW | ✅ РЕАЛИЗОВАНО | useInteractFSM.test.ts, useEdgeTooltip.test.ts:33 |

### 6.1 Новые проблемы, обнаруженные в процессе реализации

#### NEW-1 [WARNING] selectTrustline() не обновляет кэш при входе через NodeCard — ✅ РЕШЕНО в useInteractMode.ts:362
**Файл:** `simulator-ui/v2/src/composables/useInteractMode.ts` (около строки 573)

**Статус:** ✅ исправлено — `selectTrustline()` теперь вызывает `refreshParticipants()` и `refreshTrustlines()`.

Путь `onInteractEditTrustline` (из NodeCard) вызывает `selectTrustline(fromPid, toPid)`, которая переходит сразу в `editing-trustline` без вызова `refreshTrustlines()` и `refreshParticipants()`. В отличие от этого, `selectEdge()` всегда запускает оба refresh. Если кэш устарел (TTL 30 сек), TrustlineManagementPanel отобразит данные из снапшота, а не актуальные с сервера.

**Требуемое исправление:**
```typescript
function selectTrustline(fromPid: string, toPid: string) {
  if (busyRef.value) return
  clearError()
  state.fromPid = String(fromPid ?? '').trim() || null
  state.toPid = String(toPid ?? '').trim() || null
  if (!state.fromPid || !state.toPid) return
  state.selectedEdgeKey = keyEdge(state.fromPid, state.toPid)
  state.edgeAnchor = null
  state.phase = 'editing-trustline'
  // Добавить аналогично selectEdge():
  void refreshParticipants()
  void refreshTrustlines()
}
```

#### NEW-2 [LOW] Hardcoded z-index в компонентах — не используют токены — ✅ РЕШЕНО в TopBar.vue:489
**Файлы:**
- `simulator-ui/v2/src/components/EdgeDetailPopup.vue:168` — `z-index: 42` ✅
- `simulator-ui/v2/src/components/ErrorToast.vue:73` — `z-index: 200` ✅
- `simulator-ui/v2/src/components/TopBar.vue:399` — `z-index: 60` ✅
- `simulator-ui/v2/src/components/TopBar.vue:489` — Admin dropdown inline `z-index: 60` ✅

**Статус:** ✅ РЕШЕНО — все компоненты мигрированы на `--ds-z-*` токены, включая `TopBar.vue:489` (Admin dropdown).

REF-9 создал систему токенов через CSS-переменные `--ds-z-*` в `App.css` и применил их в `designSystem.overlays.css`, но scoped-стили самих компонентов не мигрированы.

**Требуемое исправление:** Заменить на переменные:
```css
/* EdgeDetailPopup.vue */ z-index: var(--ds-z-panel, 42);
/* ErrorToast.vue */ z-index: var(--ds-z-alert, 200);
/* TopBar.vue */ z-index: var(--ds-z-inset, 60);
```

#### NEW-3 [LOW] Разный источник данных для EdgeDetailPopup vs TrustlineManagementPanel — ✅ РЕШЕНО в SimulatorAppRoot.vue:255
**Файл:** `simulator-ui/v2/src/components/SimulatorAppRoot.vue:264`

**Статус:** ✅ исправлено — `interactSelectedLink` теперь предпочитает backend-fetched trustlines с fallback на snapshot.

`interactSelectedLink` вычисляется только из `state.snapshot`. `EdgeDetailPopup` получает `used/limit/available` из снапшота. `TrustlineManagementPanel` через `effectiveData` приоритизирует backend-fetched данные. При одновременном показе пользователь может видеть разные значения.

**Требуемое исправление:** Использовать `interact.mode.trustlines.value` как источник для `interactSelectedLink`, с fallback на snapshot.

---

## 7. Замечания, риски и рекомендации по тестам

### 7.1 Риски производительности: tooltip layout thrash

**Файл:** `simulator-ui/v2/src/composables/useEdgeTooltip.ts`

**Проблема:** Tooltip измеряет DOM-размеры через `querySelector` + `getBoundingClientRect()` внутри computed (вызывается на каждый pointermove / реактивный апдейт). Это потенциальный источник layout thrash — принудительный синхронный reflow на каждое движение мыши.

**Рекомендации:**
- Хранить `overlaySize` в `ref` и обновлять только при mount/изменении контента (через `ResizeObserver`).
- Либо измерять 1 раз при показе (или через `requestAnimationFrame`), а не на каждый пересчёт стиля.

**Приоритет:** MEDIUM (заметно при частых pointermove на слабых устройствах).

✅ РЕАЛИЗОВАНО: overlaySize кэшируется в ref, DOM query только при первом показе (useEdgeTooltip.ts:43). Регресс-тест: useEdgeTooltip.test.ts:34.

---

### 7.2 Контракт: `lastClearing` не сбрасывается в `resetToIdle()`

**Файл:** `simulator-ui/v2/src/composables/interact/useInteractFSM.ts`

**Наблюдение:** `resetToIdle()` сбрасывает `edgeAnchor`, `fromPid`, `toPid` и т.д., но **не сбрасывает `lastClearing`**. Если это осознанное решение — «idle может содержать lastClearing» как память о последнем клиринге — стоит явно задокументировать контракт. Если нет — логичнее чистить `lastClearing` в `resetToIdle()` для предсказуемости.

**Рекомендация:** Явно добавить комментарий в FSM (или в этот документ): «`lastClearing` сохраняется в idle намеренно — для отображения истории в BottomBar / HistoryLog».

**Приоритет:** LOW (риск UX-несоответствия, не баг).

✅ РЕАЛИЗОВАНО: JSDoc-контракт и inline-комментарий добавлены (useInteractFSM.ts:31, :105).

---

### 7.3 NodeCardOverlay: числовые поля без `renderOrDash`

**Файл:** `simulator-ui/v2/src/components/NodeCardOverlay.vue`

**Проблема:** Список trustlines в NodeCard рендерит числовые поля (`used`, `limit`, `available`) без `renderOrDash()`, в отличие от `EdgeDetailPopup.vue` и `TrustlineManagementPanel.vue`. Риск: `null` или `''` будет отображаться как пустота или «0» — рассинхрон с остальным UI.

**Рекомендация:** Выровнять форматирование: применить `renderOrDash()` из `valueFormat.ts` к числовым полям TL в NodeCard.

**Приоритет:** LOW (консистентность UX, не функциональный баг).

✅ РЕАЛИЗОВАНО: renderOrDash() применён к числовым полям TL в NodeCardOverlay.vue:173.

---

### 7.4 TrustlineManagementPanel: UX-сложность ветвлений create/edit/picking

**Файл:** `simulator-ui/v2/src/components/TrustlineManagementPanel.vue`

**Наблюдение:** Панель стала заметно умнее (effectiveUsed / валидация / синхронизация), но UX-ветвления «что показываем в create / edit / picking» теперь довольно сложные. Риск регрессий — скорее UX-логический, чем технический: пользователю может быть неочевидно, почему исчезли селекты From/To или почему «Existing» недоступен.

**Рекомендация:** Убедиться, что `title` / `disabled`-hint присутствуют везде, где селект скрывается или блокируется. Рассмотреть добавление tooltip/hint в заблокированных состояниях.

**Приоритет:** LOW (UX-polish).

✅ РЕАЛИЗОВАНО: title-атрибуты добавлены на To-селект и dropdown Existing в TrustlineManagementPanel.vue:271, :291.

---

### 7.5 Рекомендации по тестам (регресс-защита)

Следующие два кейса добавят покрытие для системных гарантий рефакторинга:

1. **«cancel() гарантированно очищает edgeAnchor и закрывает EdgeDetailPopup»** — сквозной тест на связку FSM + UI:
   - Клик на ребро → anchor установлен, popup открыт
   - `cancel()` → anchor = null, popup закрыт
   - Переход в любую другую фазу → popup не появляется

2. **«Tooltip clamp не вызывает повторных DOM-измерений при неподвижном курсоре»** — тест или проверка того, что расчёт позиции не зависит от постоянного DOM query:
   - Альтернатива: smoke-тест производительности (нет лишних reflow при mousemove без изменения контента).

**Приоритет:** LOW (nice-to-have, не блокирует релиз).

✅ РЕАЛИЗОВАНО: useInteractFSM.test.ts (cancel+edgeAnchor+lastClearing), useEdgeTooltip.test.ts:33 (overlay size caching). Все 6 тестов проходят.

---

## 5. Матрица окон: позиционирование и z-index

| Компонент | Позиция CSS | z-index | Видимость (фазы FSM) | Перекрытие |
|---|---|---|---|---|
| TopBar | `top:12px; left:12px; right:12px` | 40 | Всегда (если !bootLoading) | — |
| ActionBar | `.ds-ov-bar` (inline layout) | — | Всегда (interact mode) | — |
| SystemBalanceBar | `.ds-ov-bar` (inline layout) | — | interact + autoRun | — |
| ManualPaymentPanel | `right:12px; top:110px` | 42 | picking-payment-*, confirm-payment | ⚠ с TrustlinePanel при быстрой смене (§2.11) |
| TrustlineManagementPanel | `right:18px; top:dynamic` | 42 | picking-trustline-*, confirm-tl-create, editing-tl | ⚠ с ManualPaymentPanel (§2.11) |
| ClearingPanel | `right:12px; top:110px` | 42 | confirm-clearing, clearing-preview, clearing-running | — |
| EdgeDetailPopup | `left/top: anchor-based` | 42 | editing-trustline && anchor | ⚠ стейл anchor (§2.2, §2.9) |
| NodeCardOverlay | `left/top: node-based` | 42 | dblclick на узел (не в picking) | ⚠ с panels при dblclick в picking (§2.4) |
| LabelsOverlayLayers (world labels / floating layers) | app-specific | (см. CSS) | всегда (над canvas) | ⚠ перекрывает окна при неверном z-index (§2.15) |
| EdgeTooltip | `left/top: pointer-based` | 55 | hover на ребро | — |
| ErrorToast | `bottom:68px; left:50%` | 200 | interact + error | ⚠ с HistoryLog (§2.10) |
| InteractHistoryLog | `bottom:68px; right:12px` | 30 (ds-ov-bottom) | interact + history.length > 0 | ⚠ с ErrorToast (§2.10) |
| BottomBar | `bottom:12px; left:50%` | 30 | Всегда | — |
| DevPerfOverlay | `right:12px; bottom:12px` | 50 | Dev mode | — |

### Рекомендуемый порядок z-index:

```
z-index: 200  ErrorToast (alerts — всегда поверх)
z-index:  80  EdgeTooltip (transient — быстро уходит)
z-index:  50  DevPerfOverlay
z-index:  70  Panels + Popups (ManualPayment, Trustline, Clearing, EdgeDetail, NodeCard)
z-index:  60  World labels / floating layers (должны быть ниже окон) (§2.15)
z-index:  40  TopBar
z-index:  30  BottomBar, HistoryLog
```

**Проблемы:**
1) Panels и NodeCard на одном z-index (42). В текущей реализации FSM и guards не допускают одновременного рендеринга двух panels, но при визуальных transitions возможно кратковременное наложение.
2) EdgeDetailPopup и NodeCard теоретически не конфликтуют (NodeCard закрывается при входе в editing-trustline), но стейл anchor (§2.9) и старт flow из ActionBar при открытом NodeCard (§2.19) нарушают эту гарантию.
3) World labels / floating layers могут оказаться выше окон при несогласованной шкале z-index (§2.15) — требуется унификация токенов (§3.9).
