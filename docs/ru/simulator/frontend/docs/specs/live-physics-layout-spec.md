# Спецификация: Live Physics Layout для Graph Visualization

> **Цель**: Более просторное расположение узлов с учетом размеров экрана + живая физическая симуляция с отталкиванием узлов.
> 
> **Ограничения**: Без дополнительных зависимостей, минимальный overengineering.

## 1. Анализ текущего состояния

### Текущая архитектура

```
forceLayout.ts        → Статический force-directed layout (детерминированный, 140-180 итераций)
useLayoutCoordinator  → Координация resize + relayout
useRenderLoop         → RAF-loop для рендеринга (60fps)
```

### Проблемы (видно на скриншоте)

| Проблема | Причина |
|----------|---------|
| Узлы скучены в центре | `linkDistanceBase = k * 0.70` слишком маленький + сильная центральная гравитация |
| Не используется пространство экрана | Affine transform масштабирует только в конце, layout не "знает" о реальном viewport |
| Статичная картинка | Layout вычисляется один раз при загрузке snapshot |

### Текущие параметры (forceLayout.ts)

```typescript
const cStrength = centerStrength ?? 0.060    // Гравитация к центру
const chargeStrength = k * k * 0.020         // Отталкивание
const springStrength = 0.022                 // Пружины связей
const linkDistanceBase = k * 0.70            // Желаемая длина связи
const iterations = 180                        // Фиксированное число итераций
```

---

## 2. Предложение: Двухэтапный подход

### Этап A: Улучшение статического layout (Quick Win)

Минимальные изменения в `forceLayout.ts` для лучшего использования пространства:

```typescript
// БЫЛО:
const linkDistanceBase = Math.max(16, k * 0.70)
const cStrength = centerStrength ?? 0.060

// СТАНЕТ:
const linkDistanceBase = Math.max(24, k * 1.10)  // +57% дистанция между связанными узлами
const cStrength = centerStrength ?? 0.035        // -42% гравитация к центру
const chargeStrength = k * k * 0.035             // +75% отталкивание
```

**Эффект**: Узлы займут больше пространства без изменения архитектуры.

---

### Этап B: Live Physics Simulation (Основное предложение)

#### 2.1. Новый модуль: `usePhysicsSimulation.ts`

```typescript
export type PhysicsState = {
  // Позиции (используем TypedArrays для производительности)
  x: Float32Array
  y: Float32Array
  // Скорости
  vx: Float32Array
  vy: Float32Array
  // Радиусы узлов (для collision detection)
  r: Float32Array
  // Флаг: симуляция активна
  isRunning: boolean
  // Температура (затухание со временем)
  alpha: number
}

export type PhysicsConfig = {
  // Viewport
  width: number
  height: number
  // Силы
  centerStrength: number      // Гравитация к центру (0.02)
  repulsionStrength: number   // Отталкивание узлов (2000)
  linkStrength: number        // Пружины связей (0.03)
  linkDistance: number        // Идеальная длина связи (80)
  // Collision
  collisionStrength: number   // Сила отталкивания при overlap (0.7)
  // Damping
  velocityDecay: number       // Трение (0.4)
  alphaDecay: number          // Затухание температуры (0.0228)
  alphaMin: number            // Минимальная температура (0.001)
}

export function usePhysicsSimulation(
  initialNodes: LayoutNode[],
  links: LayoutLink[],
  config: PhysicsConfig
): {
  state: PhysicsState
  tick: () => boolean          // Один шаг симуляции, возвращает true если продолжать
  start: () => void
  stop: () => void
  reheat: () => void           // Перезапуск симуляции (при drag, resize)
  getPositions: () => Map<string, {x: number, y: number}>
}
```

#### 2.2. Физическая модель

**Силы действующие на каждый узел:**

```
F_total = F_center + F_repulsion + F_links + F_collision + F_boundary
```

1. **Center Force (гравитация к центру)**
   ```
   F_center = (center - position) * centerStrength * alpha
   ```
   - Держит граф собранным, не даёт разлететься

2. **Repulsion Force (отталкивание всех от всех)**
   ```
   F_repulsion = direction * repulsionStrength / distance²
   ```
   - Каждая пара узлов отталкивается
   - Сила убывает квадратично с расстоянием
   - **Оптимизация**: Barnes-Hut не нужен для <500 узлов

3. **Link Force (пружины связей)**
   ```
   F_link = direction * (distance - idealDistance) * linkStrength
   ```
   - Связанные узлы притягиваются/отталкиваются к идеальной дистанции

4. **Collision Force (предотвращение overlap)**
   ```
   if (distance < r1 + r2):
     F_collision = direction * (r1 + r2 - distance) * collisionStrength
   ```

5. **Boundary Force (отталкивание от краёв)**
   ```
   if (x < margin) F += (margin - x) * boundaryStrength
   if (x > width - margin) F -= (x - (width - margin)) * boundaryStrength
   // аналогично для y
   ```

#### 2.3. Интеграция скоростей (Velocity Verlet упрощённый)

```typescript
function tick(): boolean {
  if (state.alpha < config.alphaMin) {
    state.isRunning = false
    return false
  }

  // 1. Вычислить все силы
  computeForces()

  // 2. Обновить скорости с затуханием
  for (let i = 0; i < n; i++) {
    state.vx[i] = (state.vx[i] + fx[i]) * config.velocityDecay
    state.vy[i] = (state.vy[i] + fy[i]) * config.velocityDecay
  }

  // 3. Обновить позиции
  for (let i = 0; i < n; i++) {
    state.x[i] += state.vx[i]
    state.y[i] += state.vy[i]
  }

  // 4. Уменьшить температуру
  state.alpha -= (state.alpha - config.alphaMin) * config.alphaDecay

  return true
}
```

#### 2.4. Интеграция с Render Loop

```typescript
// useRenderLoop.ts - модифицированный loop

function renderFrame(nowMs: number) {
  // ... existing code ...

  // Шаг физики (может быть несколько за кадр для стабильности)
  if (physics.state.isRunning) {
    const substeps = 2
    for (let i = 0; i < substeps; i++) {
      if (!physics.tick()) break
    }
    // Обновить позиции в layout.nodes
    updateNodePositions(physics.getPositions())
  }

  // ... draw graph ...
}
```

---

## 3. Адаптация к размеру экрана

### 3.1. Динамические параметры на основе viewport

```typescript
function computeAdaptiveConfig(w: number, h: number, nodeCount: number): PhysicsConfig {
  const area = w * h
  const k = Math.sqrt(area / nodeCount)  // Идеальное расстояние между узлами

  return {
    width: w,
    height: h,
    centerStrength: 0.02,
    repulsionStrength: k * k * 0.8,       // Масштабируется с площадью
    linkStrength: 0.03,
    linkDistance: Math.max(40, k * 0.9),  // Минимум 40px, иначе пропорционально
    collisionStrength: 0.7,
    velocityDecay: 0.4,
    alphaDecay: 0.0228,
    alphaMin: 0.001,
  }
}
```

### 3.2. Reheat при resize

```typescript
// useLayoutCoordinator.ts

function onWindowResize() {
  requestResizeAndLayout()
  
  // Перезапустить физику с новыми размерами
  physics.updateConfig(computeAdaptiveConfig(layout.w, layout.h, nodeCount))
  physics.reheat()
}
```

---

## 4. Drag Interaction

```typescript
// При начале drag узла:
function onDragStart(nodeId: string) {
  physics.pinNode(nodeId)  // Зафиксировать узел в позиции курсора
  physics.reheat()         // Перезапустить симуляцию
}

// При перетаскивании:
function onDragMove(nodeId: string, x: number, y: number) {
  physics.setNodePosition(nodeId, x, y)
}

// При отпускании:
function onDragEnd(nodeId: string) {
  physics.unpinNode(nodeId)
}
```

---

## 5. Оптимизации производительности

### 5.1. Пропуск далёких пар (для repulsion)

```typescript
const maxRepulsionDistance = k * 3  // Не считать отталкивание дальше 3k

for (let i = 0; i < n; i++) {
  for (let j = i + 1; j < n; j++) {
    const dx = x[j] - x[i]
    const dy = y[j] - y[i]
    const dist2 = dx * dx + dy * dy
    
    if (dist2 > maxRepulsionDistance * maxRepulsionDistance) continue
    
    // ... compute force ...
  }
}
```

### 5.2. Frozen state (после затухания)

```typescript
// Когда alpha < alphaMin, симуляция останавливается
// RAF loop продолжает рендерить, но tick() не вызывается
// Reheat при взаимодействии пользователя
```

### 5.3. Quality levels

```typescript
type QualityLevel = 'low' | 'med' | 'high'

const substepsPerFrame: Record<QualityLevel, number> = {
  low: 1,   // Мобильные / слабые устройства
  med: 2,   // Обычный режим
  high: 3,  // Presentation mode
}
```

---

## 6. Файловая структура изменений

```
simulator-ui/v2/src/
├── layout/
│   ├── forceLayout.ts           # Существующий (модифицировать параметры)
│   └── physicsSimulation.ts     # НОВЫЙ: live physics engine
├── composables/
│   ├── useLayoutCoordinator.ts  # Модифицировать: интеграция physics
│   ├── useRenderLoop.ts         # Модифицировать: tick physics в loop
│   └── usePicking.ts            # Модифицировать: drag → physics
```

---

## 7. План реализации

### Фаза 1: Quick Win (1-2 часа)
- [ ] Изменить параметры в `forceLayout.ts` для лучшего расстояния
- [ ] Тестировать визуально

### Фаза 2: Live Physics (4-6 часов)
- [ ] Создать `physicsSimulation.ts` 
- [ ] Базовая физика: center + repulsion + links
- [ ] Интеграция с render loop
- [ ] Collision detection
- [ ] Boundary forces

### Фаза 3: Интерактивность (2-3 часа)
- [ ] Drag nodes → reheat physics
- [ ] Resize → adapt config + reheat
- [ ] Pin/unpin nodes

### Фаза 4: Polish (1-2 часа)
- [ ] Performance profiling
- [ ] Quality settings
- [ ] Tests

---

## 8. Оценка рисков

| Риск | Вероятность | Митигация |
|------|-------------|-----------|
| Производительность на 100+ узлах | Средняя | Пропуск далёких пар, substeps |
| Нестабильная симуляция | Низкая | Velocity clamping, softening |
| Регрессия детерминизма (e2e тесты) | Высокая | Флаг `isTestMode` → использовать старый статический layout |

---

## 9. Альтернативы (отклонённые)

| Альтернатива | Почему отклонена |
|--------------|------------------|
| d3-force | Дополнительная зависимость, overengineering |
| Web Workers | Сложность синхронизации, не нужно для <500 узлов |
| Barnes-Hut | Overengineering для текущего масштаба |
| WebGL physics | Огромный overhead для простой задачи |

---

## 10. Acceptance Criteria

1. ✅ Узлы занимают >70% viewport при первоначальной загрузке
2. ✅ При resize окна граф плавно адаптируется
3. ✅ Узлы не перекрываются (collision работает)
4. ✅ Drag узла → остальные расходятся
5. ✅ Симуляция затухает за ~3 секунды
6. ✅ 60fps на 100 узлах / 87 связях
7. ✅ e2e тесты не ломаются (isTestMode → детерминированный layout)
