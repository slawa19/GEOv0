# Спецификация (финал): Live Physics Layout для Graph Visualization (v2 + d3-force)

> **Решение**: Использовать `d3-force` для live-симуляции (как в v1). В `test-mode` симуляция полностью выключена.
>
> **Приоритет**: Скорость разработки и минимум отладки.

## 1. Контекст и текущая архитектура (v2)

### Текущая архитектура

```
forceLayout.ts        → Статический baseline-layout (детерминированный)
useLayoutCoordinator  → Координация resize + relayout
useRenderLoop         → RAF-loop для рендеринга
App.vue               → Интеракции (drag, selection), сборка зависимостей
```

### Целевое улучшение

| Что хотим | Почему это важно |
|---|---|
| «Живое» поведение узлов (repulsion + links + collide) | Улучшает читаемость и ощущение динамики без ручного relayout |
| Реакция на drag и resize | Узлы расходятся/перестраиваются вокруг вмешательства пользователя |
| Стабильные e2e (скриншоты) | `d3-force` по определению недетерминированен в RAF, поэтому в test-mode его выключаем |

### Базовый принцип (важно для тестов)

- **Baseline layout остаётся детерминированным** (текущий `forceLayout.ts`).
- **Live physics включается только в runtime**, и **не запускается в `test-mode`**.
- E2E screenshot tests остаются без изменений.

---

## 2. План: Baseline + Live Physics (d3-force)

### Phase A: Quick Win (пропускаем)

Baseline layout уже достаточно хороший как стартовая точка.

**Оговорка (опционально позже):** иногда полезно сделать один небольшой runtime-тюнинг baseline (только при `!isTestMode`) чтобы уменьшить «первый взрыв» при старте симуляции. Это не часть MVP.

### Phase B: Live Physics Simulation (основная фаза)

#### 2.1. Новый модуль: `physicsD3.ts`

Цель: инкапсулировать `d3-force` и дать предсказуемые операции для App.vue.

**Почему отдельный модуль:** App.vue в v2 уже большой; небольшая изоляция логики симуляции ускорит итерации без превращения в overengineering.

```ts
export type PhysicsQuality = 'low' | 'med' | 'high'

export type PhysicsConfig = {
  width: number
  height: number
  quality: PhysicsQuality

  // Forces (d3-force масштабы отличаются от кастомных)
  linkDistance: number
  linkStrength: number
  chargeStrength: number
  centerStrength: number
  collisionPadding: number

  // Cooling
  alphaMin: number
  alphaDecay: number
  velocityDecay: number
}

export type PhysicsEngine = {
  isRunning: () => boolean
  start: () => void
  stop: () => void
  reheat: (alpha?: number) => void
  tick: (substeps?: number) => void

  syncFromLayout: () => void
  syncToLayout: () => void

  pin: (nodeId: string, x: number, y: number) => void
  unpin: (nodeId: string) => void

  updateViewport: (w: number, h: number) => void
}
```

#### 2.2. Модель сил (d3-force)

Используем стандартные силы d3-force:

- `forceManyBody()` для repulsion (`strength` обычно отрицательный)
- `forceLink()` для связей (distance + strength)
- `forceCenter()` для стабилизации в центре viewport
- `forceCollide()` для предотвращения overlap (радиус считается из визуального размера узла)

**Границы (MVP):** после `tick()` делаем простой `clamp` позиций в прямоугольнике viewport с небольшим margin.

#### 2.3. Управление тиком

MVP-подход: как в v1 — вручную вызываем `simulation.tick()` внутри RAF (через render loop) и не используем `simulation.restart()` как «внутренний таймер».

**Почему так:** полностью контролируем, когда симуляция влияет на кадр; проще держать стабильный UX.

#### 2.4. Интеграция с Render Loop

Варианты интеграции (оба допустимы):

1) **Минимально (MVP):** `tick()` вызывается прямо в App.vue перед `renderOnce()` или через небольшой хук.
2) **Чище архитектурно:** добавить в `useRenderLoop.ts` опциональный callback `beforeDraw(nowMs)` и тикать там.

В обоих вариантах: в `test-mode` симуляции нет (не создаём engine).

---

## 3. Адаптация к размеру экрана (resize)

При resize:

- обновляем цели центрирующей силы (например `forceX(w/2)` + `forceY(h/2)`; см. пример `physicsD3.ts` ниже)
- при необходимости пересчитываем адаптивные `linkDistance` / `chargeStrength`
- делаем `reheat()` (поднимаем `alpha` на небольшой уровень)
- делаем простой clamp координат в пределах viewport

Замечание: heavy relayout (пересчёт baseline layout) остаётся дебаунснутым и контролируется текущим `useLayoutCoordinator`.

---

## 4. Drag Interaction (pin/unpin)

Используем стандартный d3-паттерн `fx/fy`:

- drag start: `node.fx = x; node.fy = y; reheat()`
- drag move: обновляем `fx/fy`
- drag end: если узел не должен оставаться pinned — `fx/fy = null`; затем `reheat()`

Pinned-логика должна быть согласована с текущим состоянием `pinnedPos` (если оно используется в UI).

---

## 5. Производительность и деградация качества

Для `d3-force` базовые рычаги производительности:

- снижать качество через уже существующее `quality` в UI (low/med/high)
- в low качестве уменьшать силу/количество вычислений: более быстрый cooling (больше `alphaDecay`) и меньшие значения сил
- после затухания (`alpha < alphaMin`) не тикать симуляцию вообще

Дополнительно можно использовать `snapshot.limits` как сигнал деградации (если присутствует).

---

## 6. Файлы и изменения

Минимальный набор:

```
simulator-ui/v2/
├── package.json                 # +d3-force, +@types/d3-force
└── src/
  ├── layout/
  │   └── physicsD3.ts         # NEW: d3-force engine wrapper
  ├── composables/
  │   └── useRenderLoop.ts     # optional: beforeDraw hook
  └── App.vue                  # init sim, tick, drag pin/unpin, resize reheat
```

---

## 7. План реализации

### Фаза 1: Зависимость (10–20 минут)
- [ ] Добавить `d3-force` и `@types/d3-force` в `simulator-ui/v2`

### Фаза 2: Базовая интеграция (2–3 часа)
- [ ] Создать `physicsD3.ts` и инициализировать simulation из текущих `layout.nodes/layout.links`
- [ ] Тикать `simulation.tick()` в RAF перед рендером
- [ ] После `tick()` делать clamp координат в viewport

### Фаза 3: Drag + pin/unpin (1–2 часа)
- [ ] На drag start/move выставлять `fx/fy` + `reheat()`
- [ ] На drag end — `fx/fy=null` если узел не pinned

### Фаза 4: Resize + reheat (30–60 минут)
- [ ] Обновлять `center` force под новые размеры
- [ ] `reheat()` на resize

### Фаза 5: Проверка (30–60 минут)
- [ ] Убедиться, что в `test-mode` симуляция не создаётся/не тикает
- [ ] Убедиться, что e2e screenshot tests не менялись

---

## 8. Риски и митигации

| Риск | Вероятность | Митигация |
|---|---:|---|
| Регрессия детерминизма (e2e) | Высокая | В `test-mode` не создаём/не тикаем d3-force |
| Performance на больших графах | Средняя | Cooling быстрее, остановка на `alphaMin`, деградация по quality/limits |
| «Расплывание»/уход за границы | Средняя | Простой clamp после tick (MVP) |

**Если e2e флакают:** можно добавить дополнительную защиту `navigator.webdriver` (отключать симуляцию при WebDriver). По умолчанию не используем.

---

## 9. Альтернативы

| Альтернатива | Почему не выбрана |
|---|---|
| Свой physics engine | Дольше по времени и больше отладки, чем `d3-force` |
| Web Workers | Усложнение синхронизации; MVP не требует |
| WebGL physics | Не соответствует масштабу задачи |

---

## 10. Acceptance Criteria

1. ✅ В runtime граф «оживает» и затухает за ~3 секунды
2. ✅ Узлы не перекрываются (collision работает)
3. ✅ Drag узла → остальные расходятся
4. ✅ Resize → reheat и обновление центра
5. ✅ 60fps на ~100 узлах / ~100 связях в обычном режиме
6. ✅ e2e тесты не ломаются (test-mode → только детерминированный layout, без d3-force)

---

## 11. Референсные значения параметров (из v1)

Эти значения уже проверены в `simulator-ui/v1/src/components/simulator/GeoSimulatorMesh.vue`:

```typescript
// d3-force simulation setup (из v1, адаптировать для v2)
sim = forceSimulation<GeoNode>(nodes)
  .alpha(1)                      // Начальная "температура"
  .alphaMin(0.02)                // Порог остановки
  .alphaDecay(0.04)              // Скорость затухания (~3 сек до остановки)
  .force('charge', forceManyBody().strength(-70))
  .force('center', forceCenter(canvasW / 2, canvasH / 2))
  .force('link', forceLink<GeoNode, GeoLink>(links)
    .id(d => d.id)
    .distance(100)               // Идеальная длина связи
    .strength(0.65)
  )
  .force('collide', forceCollide<GeoNode>()
    .radius(d => d.baseSize * 3) // Радиус коллизии
    .strength(0.8)
  )
  .stop()  // Управляем вручную через tick()
```

**Для v2 рекомендуемая адаптация:**

| Параметр | v1 | v2 (предложение) | Почему |
|----------|-----|-------------------|--------|
| `alpha` (start) | 1 | 0.3 | Baseline уже хороший, не нужен "взрыв" |
| `alphaMin` | 0.02 | 0.001 | Дольше "живёт", плавнее затухает |
| `alphaDecay` | 0.04 | 0.02 | Медленнее cooling, ~3 сек |
| `charge.strength` | -70 | -80 | Чуть сильнее отталкивание |
| `link.distance` | 100 | 80-120 (адаптивно) | Зависит от viewport/nodeCount |
| `link.strength` | 0.65 | 0.4 | Мягче пружины, меньше "дёргания" |
| `collide.radius` | `d.baseSize * 3` | `Math.max(d.__r ?? 15, 20)` | Из `sizeForNode()` |
| `collide.strength` | 0.8 | 0.7 | Чуть мягче |

---

## 12. Пример реализации `physicsD3.ts` (стартовый скелет)

```typescript
// src/layout/physicsD3.ts
import {
  forceSimulation,
  forceManyBody,
  forceLink,
  forceX,
  forceY,
  forceCollide,
  type Simulation,
  type SimulationNodeDatum,
  type SimulationLinkDatum,
} from 'd3-force'
import type { LayoutNode, LayoutLink } from './forceLayout'
import { sizeForNode } from '../render/nodePainter'

// d3-force требует x/y на узлах, наши LayoutNode уже имеют __x/__y
type D3Node = LayoutNode & SimulationNodeDatum & { fx?: number | null; fy?: number | null }
type D3Link = LayoutLink & SimulationLinkDatum<D3Node>

export type PhysicsConfig = {
  width: number
  height: number
  chargeStrength: number
  linkDistance: number
  linkStrength: number
  centerStrength: number
  collideRadius: (node: D3Node) => number
  collideStrength: number
  alphaStart: number
  alphaMin: number
  alphaDecay: number
}

export function createDefaultConfig(w: number, h: number, nodeCount: number): PhysicsConfig {
  const area = w * h
  const k = Math.sqrt(area / Math.max(1, nodeCount))

  return {
    width: w,
    height: h,
    chargeStrength: -80,
    linkDistance: Math.max(60, Math.min(120, k * 0.9)),
    linkStrength: 0.4,
    centerStrength: 0.05,
    collideRadius: (n) => {
      const s = sizeForNode(n)
      // Близко к тому, как считается радиус в forceLayout.ts.
      return Math.max(8, Math.max(s.w, s.h) * 0.56)
    },
    collideStrength: 0.7,
    alphaStart: 0.3,
    alphaMin: 0.001,
    alphaDecay: 0.02,
  }
}

export function createPhysicsEngine(
  nodes: LayoutNode[],
  links: LayoutLink[],
  config: PhysicsConfig
) {
  // Преобразуем __x/__y в x/y для d3-force
  const d3Nodes: D3Node[] = nodes.map(n => ({
    ...n,
    x: n.__x,
    y: n.__y,
  }))

  const d3Links: D3Link[] = links.map(l => ({
    ...l,
    source: l.source,
    target: l.target,
  }))

  const simulation: Simulation<D3Node, D3Link> = forceSimulation(d3Nodes)
    .alpha(config.alphaStart)
    .alphaMin(config.alphaMin)
    .alphaDecay(config.alphaDecay)
    .force('charge', forceManyBody<D3Node>().strength(config.chargeStrength))
    // В d3-force `forceCenter` не имеет strength(). Для регулируемой "силы к центру" используем forceX/forceY.
    .force('cx', forceX<D3Node>(config.width / 2).strength(config.centerStrength))
    .force('cy', forceY<D3Node>(config.height / 2).strength(config.centerStrength))
    .force('link', forceLink<D3Node, D3Link>(d3Links)
      .id(d => d.id)
      .distance(config.linkDistance)
      .strength(config.linkStrength)
    )
    .force('collide', forceCollide<D3Node>()
      .radius(config.collideRadius)
      .strength(config.collideStrength)
    )
    .stop() // Ручное управление через tick()

  const margin = 30

  return {
    isRunning: () => simulation.alpha() >= config.alphaMin,

    start: () => {
      simulation.alpha(config.alphaStart)
    },

    tick: (substeps = 1) => {
      for (let i = 0; i < substeps; i++) {
        simulation.tick()
      }
      // Clamp к viewport
      for (const n of d3Nodes) {
        n.x = Math.max(margin, Math.min(config.width - margin, n.x ?? config.width / 2))
        n.y = Math.max(margin, Math.min(config.height - margin, n.y ?? config.height / 2))
      }
    },

    syncToLayout: () => {
      // Обновляем исходные LayoutNode
      for (let i = 0; i < nodes.length; i++) {
        nodes[i].__x = d3Nodes[i].x ?? nodes[i].__x
        nodes[i].__y = d3Nodes[i].y ?? nodes[i].__y
      }
    },

    syncFromLayout: () => {
      // Если baseline layout был пересчитан (layout.nodes заменён), engine нужно пересоздать.
      // Но если позиции обновились in-place, можно просто синкнуть координаты обратно в d3.
      for (let i = 0; i < nodes.length; i++) {
        d3Nodes[i].x = nodes[i].__x
        d3Nodes[i].y = nodes[i].__y
      }
    },

    reheat: (alpha = 0.3) => {
      simulation.alpha(alpha)
    },

    pin: (nodeId: string, x: number, y: number) => {
      const node = d3Nodes.find(n => n.id === nodeId)
      if (node) {
        node.fx = x
        node.fy = y
      }
    },

    unpin: (nodeId: string) => {
      const node = d3Nodes.find(n => n.id === nodeId)
      if (node) {
        node.fx = null
        node.fy = null
      }
    },

    updateViewport: (w: number, h: number) => {
      config.width = w
      config.height = h
      const cx = simulation.force('cx') as ReturnType<typeof forceX>
      const cy = simulation.force('cy') as ReturnType<typeof forceY>
      cx?.x(w / 2)
      cy?.y(h / 2)
    },

    stop: () => {
      simulation.stop()
    },
  }
}

export type PhysicsEngine = ReturnType<typeof createPhysicsEngine>
```

---

## 13. Пример интеграции в App.vue

```typescript
// В App.vue setup()

import { createPhysicsEngine, createDefaultConfig, type PhysicsEngine } from './layout/physicsD3'

let physics: PhysicsEngine | null = null

// После вызова computeLayout() и получения layout.nodes/layout.links:
function initPhysics() {
  if (isTestMode.value) return // Не создаём в test-mode!
  
  const config = createDefaultConfig(layout.w, layout.h, layout.nodes.length)
  physics = createPhysicsEngine(layout.nodes, layout.links, config)
}

// В render loop (или через beforeDraw callback):
function onBeforeRender() {
  if (physics && physics.isRunning()) {
    physics.tick()
    physics.syncToLayout()
  }
}

// Drag integration:
function onDragStart(nodeId: string, x: number, y: number) {
  physics?.pin(nodeId, x, y)
  physics?.reheat(0.3)
}

function onDragMove(nodeId: string, x: number, y: number) {
  physics?.pin(nodeId, x, y)
}

function onDragEnd(nodeId: string, keepPinned: boolean) {
  if (!keepPinned) {
    physics?.unpin(nodeId)
  }
  physics?.reheat(0.1)
}

// Resize:
function onResize() {
  physics?.updateViewport(layout.w, layout.h)
  physics?.reheat(0.2)
}
```

---

## 14. Команды для старта

```bash
# 1. Установить зависимости
cd simulator-ui/v2
npm install d3-force
npm install --save-dev @types/d3-force

# 2. Создать файл
# src/layout/physicsD3.ts (скелет из секции 12)

# 3. Проверить тесты не сломаны
npm run test:e2e

# 4. Визуально проверить в браузере
npm run dev
# Открыть http://localhost:5173/?scene=A
```
