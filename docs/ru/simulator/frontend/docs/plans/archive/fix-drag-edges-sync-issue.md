# Анализ и замечания к реализации drag edges

## Статус: Реализация исправлена ✅

Код был рефакторен. Ниже — анализ текущего состояния и оставшиеся замечания.

---

## Что было исправлено

### Удалённый код:
- `dragEdgeCanvasEl` — отдельный canvas для drag edges
- `getLinkTerminationForDragScreen()` — дублирующая функция геометрии
- `renderDragEdgesWithScreenPos()` — screen-space рендер рёбер
- `dragIncidentLinks`, `dragEdgeCtx`, `dragRafId` и связанные переменные

### Новая архитектура:
1. **Позиция узла обновляется напрямую** в `layout.nodes`:
   ```javascript
   dragState.cachedNode.__x = x
   dragState.cachedNode.__y = y
   ```

2. **Рёбра рисуются на основном canvas** через `renderOnce()` с единым camera transform

3. **Camera snapshot** используется для DOM preview:
   ```javascript
   const cam = { panX: camera.panX, panY: camera.panY, zoom: camera.zoom }
   const p = { x: n.__x * cam.zoom + cam.panX, y: n.__y * cam.zoom + cam.panY }
   ```

4. **DOM и canvas обновляются в одном RAF callback** — гарантирует синхронизацию

---

## Оставшиеся замечания

### ⚠️ 1. Camera snapshot используется НЕ везде

В `scheduleDragPreview`:
```javascript
const cam = { panX: camera.panX, panY: camera.panY, zoom: camera.zoom }
const p = { x: n.__x * cam.zoom + cam.panX, y: n.__y * cam.zoom + cam.panY }
// DOM preview — использует snapshot
el.style.transform = `translate3d(${previewX}px, ${previewY}px, 0)`
// Canvas — НЕ использует тот же snapshot!
renderOnce()
```

`renderOnce()` → `useRenderLoop` → `getCamera: () => camera` — это **reactive read**, не snapshot.

**Риск (теоретический):** Если camera изменится между DOM update и canvas render, возможна рассинхронизация.

**Текущий статус:** Во время drag теперь дополнительно отключён wheel/zoom, поэтому camera не меняется — риск снят.

### ⚠️ 2. Двойной проход по рёбрам при drag

В `baseGraph.ts` при наличии `selectedNodeId`:
```javascript
// Pass 1: base
for (const link of links) { /* рисуем */ }

// Pass 2: overlay (если selectedNodeId)
if (selectedNodeId || activeEdges.size > 0) {
  for (const link of links) { /* рисуем highlight */ }
}
```

Во время drag `selectedNodeId = draggedNodeId`, поэтому incident edges рисовались бы дважды.

**Текущий статус:** Оптимизация применена — в `dragMode` overlay-pass отключён, а читаемость рёбер повышается в base-pass.

### ✅ 3. linkLod === 'focus' оптимизация работает

```javascript
getLinkLod: () => (dragState.active && dragState.dragging ? 'focus' : 'full')
```

При drag рисуются ТОЛЬКО инцидентные рёбра — это правильная оптимизация.

### ✅ 4. Мутация __x/__y работает корректно

Прямая мутация `cachedNode.__x` работает, потому что:
- Canvas рендер читает данные напрямую (не через Vue computed)
- `labelNodes` computed во время drag не критичен (node card скрыт)

---

## Рекомендуемые улучшения (опционально)

### Вариант A: Передавать camera snapshot в renderOnce

```typescript
// useRenderLoop.ts
function renderOnce(nowMs?: number, cameraOverride?: { panX: number; panY: number; zoom: number }) {
  const camera = cameraOverride ?? deps.getCamera()
  // ...
}

// App.vue
dragPreviewRafId = window.requestAnimationFrame(() => {
  const cam = { panX: camera.panX, panY: camera.panY, zoom: camera.zoom }
  // DOM preview
  el.style.transform = ...
  // Canvas с тем же snapshot
  renderOnce(undefined, cam)
})
```

### Вариант B: Текущая реализация достаточна

Если camera гарантированно не меняется в одном RAF cycle (что верно при отключённом pan во время drag), текущая реализация корректна.

---

## Вывод

**Основная проблема устранена.** Рёбра теперь синхронизированы с DOM preview благодаря:
1. Единому источнику позиции (`layout.nodes[].____x/y`)
2. Рендеру в одном RAF callback
3. Единому camera transform на основном canvas

Оставшиеся замечания — minor optimizations.

---

---

# Архивный анализ (до исправления)

## Описание проблемы

На скриншоте видно, что при перетаскивании узла:
- **DOM preview** (синий круг) находится в одном месте
- **Инцидентные рёбра** (серые линии) сходятся в другую точку (смещены вправо)

## Анализ причины

### Текущая архитектура (проблемная)

Система использует **три разных canvas слоя** и **два разных подхода к координатам**:

1. **Основной canvas** (`canvasEl`) — рисует граф в **world coordinates** с применением camera transform:
   ```javascript
   ctx.translate(camera.panX, camera.panY)
   ctx.scale(camera.zoom, camera.zoom)
   ```

2. **FX canvas** (`fxCanvasEl`) — рисует эффекты также в **world coordinates** с тем же camera transform

3. **Drag edges canvas** (`dragEdgeCanvasEl`) — рисует рёбра при drag в **screen coordinates** БЕЗ camera transform:
   ```javascript
   ctx.setTransform(dpr, 0, 0, dpr, 0, 0)  // только DPR scaling
   ```

### Корень проблемы

В `renderDragEdgesWithScreenPos` происходит **рассинхронизация чтения camera state**:

```javascript
function renderDragEdgesWithScreenPos(screenX: number, screenY: number) {
  // screenX, screenY уже вычислены через worldToScreen(dragLastWorldX, dragLastWorldY)
  // с ОДНИМ snapshot camera
  
  const draggedScreen = { x: screenX, y: screenY }
  
  for (const link of dragIncidentLinks) {
    // ЗДЕСЬ camera читается ПОВТОРНО для каждого соседа:
    const otherScreen = worldToScreen(other.__x, other.__y)  // ← может использовать другой camera state!
    ...
  }
}
```

**Проблема:** `camera` — это Vue reactive object. Между вызовами `worldToScreen` может произойти:
- Micro-task queue обработка
- Reactive effects update
- Другие асинхронные изменения camera

Даже если всё происходит в одном RAF callback, Vue reactive system может вызывать несогласованность при множественных чтениях.

### Дополнительные проблемы в текущем коде

1. **Дублирование логики геометрии** — `getLinkTerminationForDragScreen` в App.vue vs `getLinkTermination` в linkGeometry.ts
2. **Кэширование hostRect** — `dragState.hostLeft/hostTop` кэшируются при pointerdown и могут устареть
3. **Сложная цепочка RAF callbacks** — отдельные RAF для preview и edges создают race conditions
4. **Множественные системы координат** — world, screen, client смешиваются непоследовательно

## Решение: Унификация подхода

### Стратегия: Рисовать drag edges в WORLD координатах

Вместо создания отдельного screen-space канваса, рисовать drag edges на основном canvas (или его overlay) с применением того же camera transform.

### Преимущества

1. **Единый источник истины** для camera — нет рассинхронизации
2. **Переиспользование `getLinkTermination`** из linkGeometry.ts — нет дублирования
3. **Упрощение кода** — убираем `getLinkTerminationForDragScreen`, отдельный canvas
4. **Гарантированная синхронизация** — edges и DOM preview используют один transform

---

## Пошаговая инструкция по исправлению

### Шаг 1: Создать временную world-позицию для drag узла

Вместо отдельного `dragLastWorldX/Y`, временно обновлять позицию узла в `layout.nodes`:

```typescript
// В onCanvasPointerMove, вместо:
// dragLastWorldX = x
// dragLastWorldY = y

// Делать:
if (dragState.cachedNode) {
  dragState.cachedNode.__x = x
  dragState.cachedNode.__y = y
}
```

### Шаг 2: Убрать drag edges canvas

Удалить `dragEdgeCanvasEl` и всю связанную логику:
- `ensureDragEdgeCanvasSized()`
- `clearDragEdgeCanvas()`
- `renderDragEdgesWithScreenPos()`
- `getLinkTerminationForDragScreen()`
- `dragEdgeCtx`
- `dragIncidentLinks`

### Шаг 3: Рисовать drag edges в renderLoop

Модифицировать `useRenderLoop` или `drawBaseGraph` чтобы рисовать инцидентные рёбра для `draggedNodeId` с его **текущей** (временной) позицией:

```typescript
// В drawBaseGraph, для hidden node:
// Вместо skip — рисуем рёбра К этому узлу, используя его временную позицию
if (hiddenNodeId && (link.source === hiddenNodeId || link.target === hiddenNodeId)) {
  // Рисуем ребро с временной позицией hidden узла
  const a = link.source === hiddenNodeId 
    ? { ...pos.get(hiddenNodeId)!, __x: dragWorldX, __y: dragWorldY }
    : pos.get(link.source)!
  const b = link.target === hiddenNodeId
    ? { ...pos.get(hiddenNodeId)!, __x: dragWorldX, __y: dragWorldY }
    : pos.get(link.target)!
  // ... рисуем ребро ...
  continue
}
```

### Шаг 4: Упростить scheduleDragPreviewAtWorld

```typescript
function scheduleDragPreview() {
  if (dragPreviewPending) return
  dragPreviewPending = true
  
  dragPreviewRafId = window.requestAnimationFrame(() => {
    dragPreviewPending = false
    
    // Позиция берётся из узла (уже обновлена в pointermove)
    const n = dragState.cachedNode
    if (!n) return
    
    const p = worldToScreen(n.__x, n.__y)
    
    const previewX = p.x - dragPreviewW / 2
    const previewY = p.y - dragPreviewH / 2
    
    dragPreviewEl.value!.style.transform = `translate3d(${previewX}px, ${previewY}px, 0)`
    
    // Рёбра рисуются автоматически через renderOnce()
    renderOnce()
  })
}
```

### Шаг 5: Обновить useRenderLoop deps

Добавить новый параметр для передачи drag world position:

```typescript
type UseRenderLoopDeps = {
  // ...existing deps...
  
  // Новые deps для drag:
  getDragNodePosition?: () => { id: string; x: number; y: number } | null
}
```

### Шаг 6: Модифицировать drawBaseGraph

Добавить параметр `dragNodePosition` и использовать его для корректного рендера:

```typescript
export function drawBaseGraph(ctx: CanvasRenderingContext2D, opts: {
  // ...existing opts...
  dragNodePosition?: { id: string; x: number; y: number } | null
}) {
  const dragPos = opts.dragNodePosition
  
  // Создаём временную map позиций с override для drag узла
  const pos = new Map(nodes.map((n) => {
    if (dragPos && n.id === dragPos.id) {
      return [n.id, { ...n, __x: dragPos.x, __y: dragPos.y }]
    }
    return [n.id, n]
  }))
  
  // Остальной код использует pos как обычно
  // Рёбра к drag узлу будут рисоваться с корректной позицией
}
```

---

## Упрощённый вариант (Quick Fix)

Если полная рефакторизация слишком объёмна, можно сделать минимальное исправление:

### Quick Fix: Снять snapshot camera перед рендером

```typescript
function renderDragEdgesWithScreenPos(screenX: number, screenY: number) {
  if (!dragState.active || !dragState.dragging) return
  
  // SNAPSHOT camera state ОДИН РАЗ
  const cam = { 
    panX: camera.panX, 
    panY: camera.panY, 
    zoom: camera.zoom 
  }
  
  // Вспомогательная функция с frozen camera
  const worldToScreenFrozen = (x: number, y: number) => ({
    x: x * cam.zoom + cam.panX,
    y: y * cam.zoom + cam.panY,
  })
  
  const draggedScreen = { x: screenX, y: screenY }
  
  for (const link of dragIncidentLinks) {
    const otherId = link.source === nodeId ? link.target : link.source
    const other = layout.nodes.find((n) => n.id === otherId)
    if (!other) continue

    // Используем frozen transform
    const otherScreen = worldToScreenFrozen(other.__x, other.__y)
    
    const start = getLinkTerminationForDragScreen(baseNode, draggedScreen, otherScreen)
    const end = getLinkTerminationForDragScreen(other, otherScreen, draggedScreen)
    
    // ... draw ...
  }
}
```

**И исправить** `scheduleDragPreviewAtWorld` аналогично:

```typescript
function scheduleDragPreviewAtWorld() {
  // ...
  dragPreviewRafId = window.requestAnimationFrame(() => {
    // SNAPSHOT camera
    const cam = { 
      panX: camera.panX, 
      panY: camera.panY, 
      zoom: camera.zoom 
    }
    
    const worldToScreenFrozen = (x: number, y: number) => ({
      x: x * cam.zoom + cam.panX,
      y: y * cam.zoom + cam.panY,
    })
    
    // Вычисляем ВСЕ позиции с одним snapshot
    const dragScreen = worldToScreenFrozen(dragLastWorldX, dragLastWorldY)
    
    // DOM preview
    el.style.transform = `translate3d(${dragScreen.x - dragPreviewW/2}px, ${dragScreen.y - dragPreviewH/2}px, 0)`
    
    // Edges с тем же snapshot
    renderDragEdgesWithCameraSnapshot(dragScreen.x, dragScreen.y, cam)
  })
}
```

---

## Рекомендуемый подход

**Для долгосрочного решения:** Полная рефакторизация (Шаги 1-6)
- Устраняет дублирование кода
- Единая система координат
- Проще поддерживать

**Для быстрого исправления:** Quick Fix с camera snapshot
- Минимальные изменения
- Устраняет симптом, но не упрощает архитектуру

---

## Тестирование после исправления

1. **Базовый drag** — перетащить узел, убедиться что рёбра следуют за preview
2. **Drag при zoom** — приблизить/отдалить, затем перетащить узел
3. **Drag при pan** — сместить камеру, затем перетащить узел
4. **Быстрый drag** — быстро двигать мышью, проверить отсутствие лагов
5. **Multi-edge node** — перетащить узел с множеством рёбер
6. **Playwright tests** — убедиться что screenshot тесты проходят

## Файлы для изменения

- `simulator-ui/v2/src/App.vue` — основная логика drag
- `simulator-ui/v2/src/composables/useRenderLoop.ts` — render loop (опционально)
- `simulator-ui/v2/src/render/baseGraph.ts` — отрисовка графа (опционально)
- Удалить: логику drag edges canvas, `getLinkTerminationForDragScreen`
