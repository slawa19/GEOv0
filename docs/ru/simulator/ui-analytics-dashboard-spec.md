# Спецификация: Analytics Dashboard для Simulator UI

**Версия:** 1.0  
**Дата:** 2026-01-30  
**Статус:** Draft

---

## 1. Цели и контекст

### 1.1 Цель проекта Simulator
Симуляция реального community (100+ участников) для:
- Выявления bottlenecks в сети
- Анализа эффективности протокола клиринга
- Тестирования различных топологий сети

### 1.2 Текущие проблемы UI
1. **Перегруженность графа** — 100 узлов создают визуальный шум
2. **Отсутствие аналитики** — нет наглядных метрик эффективности
3. **Bottlenecks не видны** — данные есть в backend, но не отображаются

### 1.3 Цель доработки
Добавить Analytics Panel с использованием **существующих контрактов backend** без изменения визуальной логики графа.

---

## 2. Существующие контракты (использовать как есть)

### 2.1 Backend API

#### GET /simulator/runs/{run_id}/metrics
```typescript
// schemas/simulator.py: MetricsResponse
{
  run_id: string
  equivalent: string
  series: [
    { key: "success_rate", unit: "%", points: [{t_ms, v}] },
    { key: "avg_route_length", unit: "count", points: [...] },
    { key: "total_debt", unit: "amount", points: [...] },
    { key: "clearing_volume", unit: "amount", points: [...] },
    { key: "bottlenecks_score", unit: "%", points: [...] }
  ]
}
```

#### GET /simulator/runs/{run_id}/bottlenecks
```typescript
// schemas/simulator.py: BottlenecksResponse
{
  run_id: string
  equivalent: string
  items: [
    {
      target: { kind: "edge", from: "Alice", to: "Bob" },
      score: 0.85,
      reason_code: "FREQUENT_ABORTS" | "TOO_MANY_TIMEOUTS" | "LOW_AVAILABLE" | ...,
      label: "Frequent failures",
      suggested_action: "Increase trust limits or add alternative routes"
    }
  ]
}
```

#### RunStatus (уже используется в RealHudTop)
```typescript
{
  state: "running" | "paused" | ...
  errors_total: number
  errors_last_1m: number
  ops_sec: number
  queue_depth: number
  last_error: { code, message, at }
}
```

### 2.2 Визуализация узлов (НЕ МЕНЯТЬ)

| Поле | Источник | Визуализация |
|------|----------|--------------|
| `viz_color_key` | Backend | `debt-0`..`debt-8` → зелёный→красный |
| `viz_size` | Backend | `{w, h}` пиксели |
| `net_sign` | Backend | -1 (debtor) / 0 / +1 (creditor) |
| `type` | Backend | `business` (квадрат) / `person` (круг) |

### 2.3 Визуализация рёбер (НЕ МЕНЯТЬ)

| Поле | Источник | Визуализация |
|------|----------|--------------|
| `viz_width_key` | Backend | `hairline`/`thin`/`mid`/`thick`/`highlight` |
| `viz_alpha_key` | Backend | `bg`/`muted`/`active`/`hi` |
| `used` / `available` | Backend | Tooltip при hover |

### 2.4 FX эффекты (НЕ МЕНЯТЬ)

| Событие | Эффект |
|---------|--------|
| `tx.updated` | Cyan spark по edges |
| `clearing.plan` + `clearing.done` | Gold spark по cycle |
| `tx.failed` | Red label на target node |

---

## 3. Архитектура изменений

### 3.1 Новые компоненты

```
simulator-ui/v2/src/components/
├── RealMetricsPanel.vue      # NEW: Боковая панель аналитики
├── MetricsKpiCard.vue        # NEW: Карточка KPI
├── BottlenecksList.vue       # NEW: Список bottlenecks
└── ... (существующие)
```

### 3.2 Изменения в SimulatorAppRoot.vue

```vue
<template>
  <div class="root" :class="{ 'with-panel': showMetricsPanel }">
    <div class="graph-area">
      <!-- существующие canvas -->
    </div>
    
    <RealMetricsPanel
      v-if="apiMode === 'real' && showMetricsPanel"
      :metrics="metricsData"
      :bottlenecks="bottlenecksData"
      :run-status="real.runStatus"
      @focus-bottleneck="handleFocusBottleneck"
    />
    
    <!-- существующие HUD компоненты -->
  </div>
</template>

<style>
.root.with-panel {
  display: grid;
  grid-template-columns: 1fr 320px;
}
</style>
```

### 3.3 Новый composable: useMetricsPolling

```typescript
// composables/useMetricsPolling.ts
export function useMetricsPolling(deps: {
  runId: Ref<string | null>
  equivalent: Ref<string>
  isRunning: Ref<boolean>
  apiBase: string
  accessToken: string
}) {
  const metrics = ref<MetricsResponse | null>(null)
  const bottlenecks = ref<BottlenecksResponse | null>(null)
  
  // Poll every 5s while running
  watchEffect(() => { ... })
  
  return { metrics, bottlenecks }
}
```

---

## 4. UI компоненты

### 4.1 RealMetricsPanel.vue

**Layout:**
```
┌─────────────────────────────┐
│ ⚡ ANALYTICS                │
├─────────────────────────────┤
│  Success Rate               │
│  ████████░░  87%           │
│                             │
│  Clearing Volume            │
│  4,250 UAH                  │
│                             │
│  Avg Route Length           │
│  2.4 hops                   │
│                             │
│  Total Debt                 │
│  12,500 UAH                 │
├─────────────────────────────┤
│ ⚠ BOTTLENECKS (3)          │
├─────────────────────────────┤
│  Alice → Bob          85%   │
│  FREQUENT_ABORTS            │
│  [Focus]                    │
│                             │
│  Hub-A → Carol        72%   │
│  TOO_MANY_TIMEOUTS          │
│  [Focus]                    │
└─────────────────────────────┘
```

**Props:**
```typescript
type Props = {
  metrics: MetricsResponse | null
  bottlenecks: BottlenecksResponse | null
  runStatus: RunStatus | null
}
```

**Events:**
```typescript
type Emits = {
  'focus-bottleneck': [target: BottleneckTarget]
}
```

### 4.2 Focus на bottleneck

При клике на bottleneck:
1. Вызвать `emit('focus-bottleneck', item.target)`
2. В SimulatorAppRoot: найти edge в layout, вызвать `cameraSystem.focusOnEdge(source, target)`
3. Подсветить edge через существующий `addActiveEdge(key, ttlMs)`

---

## 5. Типографика

### 5.1 Текущие стили (сохранить)
- Font family: system-ui (через CSS vars)
- `.mono` класс для ID/чисел

### 5.2 Добавить стили для панели

```css
.metrics-panel {
  font-family: var(--font-sans, system-ui);
  font-size: 12px;
  background: var(--bg-surface, #1e293b);
  border-left: 1px solid var(--border, #334155);
}

.kpi-value {
  font-family: var(--font-mono, 'JetBrains Mono', monospace);
  font-size: 20px;
  font-weight: 600;
}

.kpi-label {
  font-size: 11px;
  color: var(--text-muted, #94a3b8);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.bottleneck-item {
  padding: 8px 12px;
  border-radius: 4px;
  background: var(--bg-elevated, #334155);
}

.bottleneck-score {
  font-family: var(--font-mono);
  font-size: 14px;
  font-weight: 600;
}

.bottleneck-score.high { color: #ef4444; }   /* score >= 0.6 */
.bottleneck-score.medium { color: #f59e0b; } /* score >= 0.3 */
.bottleneck-score.low { color: #22c55e; }    /* score < 0.3 */
```

---

## 6. Что потребует доработки

### 6.1 Backend (минимально)

| Задача | Файл | Сложность |
|--------|------|-----------|
| API endpoint `/runs/{run_id}/metrics` | `app/api/v1/simulator.py` | Low |
| API endpoint `/runs/{run_id}/bottlenecks` | `app/api/v1/simulator.py` | Low |
| Данные уже пишутся в DB | `storage.py:write_tick_metrics` | ✅ Done |
| Данные уже пишутся в DB | `storage.py:write_tick_bottlenecks` | ✅ Done |

**Примечание:** Schemas `MetricsResponse` и `BottlenecksResponse` уже определены в `schemas/simulator.py`.

### 6.2 Frontend

| Задача | Файл | Сложность |
|--------|------|-----------|
| `RealMetricsPanel.vue` | NEW | Medium |
| `MetricsKpiCard.vue` | NEW | Low |
| `BottlenecksList.vue` | NEW | Low |
| `useMetricsPolling.ts` | NEW | Medium |
| API клиент `getMetrics()` | `api/simulatorApi.ts` | Low |
| API клиент `getBottlenecks()` | `api/simulatorApi.ts` | Low |
| Grid layout в `SimulatorAppRoot` | MODIFY | Low |
| Toggle button в `RealHudBottom` | MODIFY | Low |
| Focus camera на edge | MODIFY `useAppViewWiring` | Medium |

### 6.3 НЕ ТРЕБУЕТСЯ менять

- `vizMapping.ts` — используем существующие цвета
- `nodePainter.ts` — логика отрисовки узлов
- `forceLayout.ts` — алгоритм layout (уже исправлен spacing)
- `fxRenderer.ts` — эффекты анимации
- Backend schema — все типы уже определены

---

## 7. API Endpoints (реализация)

### 7.1 GET /simulator/runs/{run_id}/metrics

```python
# app/api/v1/simulator.py

@router.get("/runs/{run_id}/metrics", response_model=MetricsResponse)
async def get_run_metrics(
    run_id: str,
    equivalent: str = Query(default="UAH"),
    from_ms: int = Query(default=0, ge=0),
    to_ms: int = Query(default=None),
    session: AsyncSession = Depends(get_db),
):
    """Return time-series metrics for a run."""
    # SELECT key, t_ms, value FROM simulator_run_metrics
    # WHERE run_id=:run_id AND equivalent_code=:equivalent
    # AND t_ms BETWEEN :from_ms AND :to_ms
    # ORDER BY t_ms
    ...
```

### 7.2 GET /simulator/runs/{run_id}/bottlenecks

```python
@router.get("/runs/{run_id}/bottlenecks", response_model=BottlenecksResponse)
async def get_run_bottlenecks(
    run_id: str,
    equivalent: str = Query(default="UAH"),
    limit: int = Query(default=10, ge=1, le=50),
    session: AsyncSession = Depends(get_db),
):
    """Return top bottlenecks for a run."""
    # SELECT * FROM simulator_run_bottlenecks
    # WHERE run_id=:run_id AND equivalent_code=:equivalent
    # ORDER BY score DESC, computed_at DESC
    # LIMIT :limit
    ...
```

---

## 8. Acceptance Criteria

1. ✅ Панель показывает 4 KPI: Success Rate, Clearing Volume, Avg Route Length, Total Debt
2. ✅ Панель показывает список bottlenecks с score и reason
3. ✅ Клик на bottleneck центрирует камеру на edge
4. ✅ Edge подсвечивается через существующий `activeEdges` механизм
5. ✅ Данные обновляются каждые 5 секунд пока run=running
6. ✅ Панель скрывается/показывается кнопкой
7. ✅ Стили соответствуют существующему темному theme
8. ✅ Не меняется логика визуализации узлов/рёбер
