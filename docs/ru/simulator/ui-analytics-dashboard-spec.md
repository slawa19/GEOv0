# Спецификация: Analytics Dashboard для Simulator UI

**Версия:** 1.1  
**Дата:** 2026-01-30  
**Последнее обновление:** 2026-08-21 (T706 программы 007)  
**Статус:** Draft — живая продуктовая спецификация экрана

> **Исполняемый контракт — [`specs/007-simulator-analytics-surface/spec.md`](../../../specs/007-simulator-analytics-surface/spec.md).**
>
> Панель построена (T700–T705, 2026-08-21). Документ сверен с построенным: форма ответов §2.1,
> размещение и состояния панели §3.2/§4.3, раскладка фокуса §4.2, стили §5, обязательные параметры
> §7.1, имена компонентов. Прежние врезки — «две поправки», пятизначный `MetricSeriesKey`, дрейф
> имён `RealHudTop`/`RealHudBottom` — сняты, потому что применены в тексте.
>
> Границы авторитета: wire-schema — [`api/openapi.yaml`](../../../api/openapi.yaml),
> потребительское пояснение для фронтенда —
> [`frontend/docs/api.md`](frontend/docs/api.md) §7.4. Здесь описывается **экран**, а не
> параллельная схема; при расхождении правится этот документ.

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
// app/schemas/simulator.py:512 MetricsResponse; канон — api/openapi.yaml:4773
{
  api_version: string          // "simulator-api/1"
  run_id: string
  equivalent: string
  from_ms: number              // эхо запрошенного окна, в собственных часах прогона (t_ms)
  to_ms: number
  step_ms: number
  series: [
    { key: "success_rate",        unit: "%",      points: [{ t_ms, v }] },
    { key: "avg_route_length",    unit: "count",  points: [...] },
    { key: "total_debt",          unit: "amount", points: [...] },
    { key: "clearing_volume",     unit: "amount", points: [...] },
    { key: "bottlenecks_score",   unit: "%",      points: [...] },
    { key: "active_participants", unit: "count",  points: [...] },
    { key: "active_trustlines",   unit: "count",  points: [...] }
  ]
}
```

**Ключей семь, а не пять.** `active_participants` и `active_trustlines` — кардинальности сети
(сколько участников активно, сколько рёбер в эквиваленте), поэтому их единица `count`, а не
`amount`: это не деньги. Согласовано во всех четырёх местах — производство
`app/core/simulator/metrics_bottlenecks.py:169-182`, pydantic `app/schemas/simulator.py:490-498`,
канон `api/openapi.yaml:4743-4755`, типы UI `simulator-ui/v2/src/api/simulatorTypes.ts:328-335`.
Ответ вправе нести **меньше** семи серий, поэтому панель пересекает свой порядок показа с тем, что
реально пришло, и не индексирует вслепую (`RealMetricsPanel.vue:34-53`).

**Значение точки `v` — decimal string или `null`, и это два разных утверждения.**

| `v` | Смысл | Как показывать |
|---|---|---|
| `null` | измерения в этой точке не было | разрыв линии; в заголовке карточки — прочерк |
| `"0.00000000"` | измерен ноль | точка на нуле; в заголовке — `0` |

Обе стороны держат эту границу намеренно: pydantic `app/schemas/simulator.py:471-487`, канон
`api/openapi.yaml:4720-4741`, типы `simulatorTypes.ts:344-352`, декодер
`simulator-ui/v2/src/api/simulatorContracts.ts:590-605` (отсутствие ключа `v` — не то же, что
`null`, и отклоняется). Бэкенд ресемплит персистированные тики carry-forward'ом, **начиная с
первого реального измерения**: точки до него несут `null`, а не выдуманный ноль
(`metrics_bottlenecks.py:219-236`).

**`v` не парсится в число.** Две из семи серий (`total_debt`, `clearing_volume`) — суммы в
выбранном эквиваленте, то есть деньги; парс в JS-число — ровно то место, где точность теряется
(AGENTS.md §8). Карточка сравнивает и масштабирует значения через `BigInt` и переводит в `Number`
только итоговое отношение 0..1000 для координаты пикселя (`MetricsKpiCard.vue:43-60,131-133`).

#### GET /simulator/runs/{run_id}/bottlenecks
```typescript
// app/schemas/simulator.py:565 BottlenecksResponse; канон — api/openapi.yaml:4850
{
  api_version: string
  run_id: string
  equivalent: string
  items: [
    {
      target: { kind: "edge", from: "Alice", to: "Bob" },   // либо { kind: "node", id }
      score: 0.85,                                          // 0..1, обычное число, не деньги
      reason_code: "FREQUENT_ABORTS" | "TOO_MANY_TIMEOUTS" | "LOW_AVAILABLE" | ...,
      label: "Frequent failures",
      suggested_action: "Increase trust limits or add alternative routes"
    }
  ]
}
```

#### Отказ вместо синтетики (оба эндпоинта)

В реальном режиме измерений либо нет, либо они настоящие: подмены синтетикой не бывает
(предусловие F-007-1 программы 007). Когда измерений нет, оба эндпоинта отвечают **503** с
`ErrorEnvelope`, и `error.details.reason` несёт причину — `storage_disabled` (персистенция
выключена) или `db_read_failed` (хранилище не прочиталось). Это тот же токен, что ушёл в лог, то
есть корреляционная ручка. См. `app/core/simulator/metrics_bottlenecks.py:34-44,91-139` и точки
подъёма `:188`, `:250`, `:349`, `:422`.

#### RunStatus (уже используется в `TopBar.vue`)
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
| `clearing.done` | Gold spark по cycle |
| `tx.failed` | Red label на target node |

---

## 3. Архитектура изменений

### 3.1 Компоненты панели

```
simulator-ui/v2/src/components/
├── RealMetricsPanel.vue      # Панель аналитики: состояния, состав, проброс focus
├── MetricsKpiCard.vue        # Карточка одной серии: значение + спарклайн
├── BottlenecksList.vue       # Список bottlenecks
└── ... (существующие)

simulator-ui/v2/src/composables/
└── useMetricsPolling.ts      # Единственный владелец опроса и фазы потока
```

Панель **не опрашивает сама**: она получает готовую фазу и данные пропсами. Один владелец потока —
одна версия правды о том, есть данные или нет.

### 3.2 Размещение в SimulatorAppRoot.vue

Панель — **зарегистрированная overlay-поверхность, а не колонка grid**. Граф не пересобирается под
неё: панель док'ается к правому краю поверх канвы, отступая от верхнего и нижнего HUD по тем же
токенам, которыми эти стеки себя измеряют.

```vue
<template>
  <!-- геометрия дока целиком из дескриптора каталога -->
  <div
    v-if="analytics.isVisible.value"
    class="sar-analytics-dock"
    data-surface="real-metrics-panel"
    :style="analyticsDockStyle"
  >
    <RealMetricsPanel
      :phase="analytics.phase.value"
      :metrics="analytics.metrics.value"
      :bottlenecks="analytics.bottlenecks.value"
      :last-error="analytics.lastError.value"
      :unavailable-reason="analytics.unavailableReason.value"
      :get-node-name="(id) => getNodeById(id)?.name ?? null"
      @focus-bottleneck="focusBottleneckTarget"
    />
  </div>
</template>
```

- дескриптор поверхности — `simulator-ui/v2/src/ui-kit/overlaySurfaceCatalog.ts:309-335`
  (`edge: 'right'`, клиренсы `--ds-hud-stack-height` / `--ds-hud-bottom-stack-height`,
  ширина `--ds-ov-panel-maxw`, слой `--ds-z-panel`);
- точка монтирования — `SimulatorAppRoot.vue:1414-1431`, правило дока `:1497-1512` объявляет только
  свойства, значения приходят из `resolveOverlayDockStyle('real-metrics-panel')`;
- перенести панель = отредактировать дескриптор, а не компонент.

**Видимость.** `analytics.isVisible` = «реальный режим» **и** «пользователь открыл панель»
(`useSimulatorApp.ts:1729-1731`). На фикстурах панель не показывается: за ней нет хранилища метрик,
и она показывала бы вечное «нет измерений».

### 3.3 Composable: useMetricsPolling

```typescript
// composables/useMetricsPolling.ts:93
export function useMetricsPolling(deps: {
  apiBase: Readonly<Ref<string>>
  accessToken: Readonly<Ref<string | null | undefined>>
  runId: Readonly<Ref<string | null | undefined>>
  equivalent: Readonly<Ref<string>>
  runStatus: Readonly<Ref<RunStatus | null | undefined>>
  /** Вторая калитка: смотрит ли кто-нибудь. Отсутствие = «поверхности нет», опроса нет вовсе. */
  enabled?: Readonly<Ref<boolean>>
}): {
  metrics: ShallowRef<MetricsResponse | null>
  bottlenecks: ShallowRef<BottlenecksResponse | null>
  phase: ComputedRef<MetricsStreamPhase>
  lastError: Ref<string>          // непусто только в `error`
  unavailableReason: Ref<string>  // непусто только в `unavailable`
  // ...
}
```

**Скрытая панель не опрашивает.** Гейт `enabled` — это ровно видимость панели, а не «реальный
режим»: за каждым опросом `GET /metrics` и `GET /bottlenecks` бэкенд идёт в таблицы метрик прогона,
поэтому пара запросов каждые пять секунд для закрытой поверхности — оплаченная работа за экран, на
который никто не смотрит (`useMetricsPolling.ts:99-107,160-168`; связывание —
`useSimulatorApp.ts:1733-1746`). Гейт по `runStatus` отвечает на другой вопрос — производятся ли
вообще новые точки: опрашивается только `running` (`:158`).

Окно запрашивается в **часах прогона**, а не в стенных: персистированные точки ключуются по
`t_ms = run.sim_time_ms`, который стартует с нуля (`useMetricsPolling.ts:55-73`). Две ручки летят
`Promise.allSettled`, а не `all`: недоступность одной не должна выглядеть как отказ другой
(`:249-259`).

---

## 4. UI компоненты

### 4.1 RealMetricsPanel.vue

**Layout:**
```
┌─────────────────────────────┐
│ ANALYTICS               UAH │
├─────────────────────────────┤
│  SUCCESS RATE               │
│  87 %          ╭─╮_╭──      │   ← карточка = значение + спарклайн
│                             │
│  CLEARING VOLUME            │
│  4250.5 UAH    ╭──╯         │
│                             │
│  … всего до семи серий, в порядке показа §2.1
├─────────────────────────────┤
│ BOTTLENECKS                 │
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

**Props** (`RealMetricsPanel.vue:16-26`):
```typescript
type Props = {
  phase: MetricsStreamPhase        // единственное состояние потока; панель сама не опрашивает
  metrics: MetricsResponse | null
  bottlenecks: BottlenecksResponse | null
  lastError?: string               // непусто только в `error`
  unavailableReason?: string       // непусто только в `unavailable`
  getNodeName?: (id: string) => string | null
}
```

**Events:**
```typescript
type Emits = {
  'focus-bottleneck': [target: BottleneckTarget]
}
```

### 4.2 Focus на bottleneck

При клике на строку bottleneck'а:

1. `BottlenecksList` эмитит `focus-bottleneck` с `item.target`, панель пробрасывает выше
   (`BottlenecksList.vue:77-86`, `RealMetricsPanel.vue:147-151`).
2. `SimulatorAppRoot.focusBottleneckTarget` (`:853-866`) разводит два вида цели: `kind: "edge"` →
   кадрирование ребра, `kind: "node"` → открытие карточки узла. Ответ виден в обоих случаях.
3. Кадрирование ребра идёт **двумя слоями**, и это осознанно:
   - `useAppViewWiring.focusOnEdge(fromId, toId)` (`:86-89`) знает идентичность: находит ребро
     `from → to` в текущем layout — направление значимо, `A → B` и `B → A` разные рёбра — и
     разрешает его в две точки. Ребра нет в снапшоте (удалено, снапшот сменился между опросом
     панели и кликом) → возвращает `false`, камера не двигается, исключения нет.
   - `cameraSystem.focusOnEdge(a, b)` (`useCamera.ts:185-204`) принимает **две точки**, а не пару
     id: камера дженерик и знает только `__x`/`__y`. Она вписывает отрезок — центр на середине,
     максимальный интерактивный зум, при котором оба конца остаются в паддинге вьюпорта.

   Прежняя редакция обещала `cameraSystem.focusOnEdge(source, target)` с идентификаторами. Так не
   сделано намеренно: разрешение «ребро → две точки» требует знания снапшота, а камера его не имеет
   и не должна иметь.
4. Подсветка ребра через существующий `addActiveEdge(key, ttlMs)` — **требование в силе, но не
   реализовано.** `focusBottleneckTarget` (`SimulatorAppRoot.vue:853-866`) только двигает камеру;
   `addActiveEdge` из этого пути не вызывается (вызовы есть только в
   `useSimulatorApp.ts:670` и `realFx/useRealClearingFx.ts:202`). Пункт оставлен как требование:
   после кадрирования пользователь всё ещё должен видеть, **какое именно** ребро ему показали.

### 4.3 Состояния панели

Состояний **три**, а не два, и различие между вторым и третьим — суть предусловия F-007-1.

| Состояние | Что произошло | Как выглядит |
|---|---|---|
| `ready` | бэкенд ответил измерениями | карточки серий + список bottlenecks |
| `unavailable` | измерений нет, и бэкенд **отказался подменять их синтетикой** (HTTP 503, причина `storage_disabled` / `db_read_failed`) | нейтральная информационная плашка «No measurements recorded» + текст причины + сам токен причины моноширинным, `role="status"` |
| `error` | всё остальное: запрос действительно не прошёл | плашка отказа, `role="alert"` |

Плюс два состояния до первого ответа: `loading` (запрос в полёте) и `idle` (калитка закрыта — нет
прогона, прогон не `running`, или панель скрыта).

**`unavailable` — не ошибка и не должно так выглядеть.** Это система, которая говорит правду:
измерений нет, и она не станет рисовать правдоподобный график вместо них. Показывать это красной
плашкой значило бы сообщать пользователю, что что-то сломалось, тогда как сломаться было бы —
показать выдуманные данные. Ветвление: `useMetricsPolling.ts:40-53` (определение фаз), `:213-220`
(503 уходит в `unavailable`, всё прочее в `error`), `:81-91` (извлечение `reason` из тела);
отрисовка — `RealMetricsPanel.vue:99-131`, тексты причин `:64-75`.

Сведение двух ручек в одну фазу (`useMetricsPolling.ts:176-184`): `error` перевешивает
`unavailable`, `unavailable` перевешивает `ready`. Устаревшие числа рядом с плашкой отказа читаются
как текущие, поэтому при неуспехе данные ручки сбрасываются в `null` (`:210-211`).

### 4.4 MetricsKpiCard.vue: как рисуется ряд

- **`null` — разрыв, а не ноль.** Точка с `v: null` завершает текущий отрезок полилинии; ряд
  рисуется набором отрезков, и «измерения не было» видно как отсутствие линии над этой отметкой
  времени (`MetricsKpiCard.vue:121-127,152-157`). Измеренный ноль `"0.00000000"` — обычная точка,
  просто на нуле.
- **Заголовок карточки берёт последнюю точку и при `null` показывает прочерк** (`—`), а не
  переносит предыдущее измерение вперёд (`:174-191`). Carry-forward — ровно та подделка, которую
  бэкенд перестал делать (T711); вернуть её в карточку значило бы вернуть ложь на экран.
- **Значение вне контракта провода не рисуется как разрыв — оно помечается отдельно.** Если
  значение не соответствует форме decimal string, карточка не строит линию и пишет «Series value
  outside the wire contract» (`:31-36,93-95,240`). «Данных нет» и «данные испорчены» — разные
  утверждения, и подмена первого вторым скрыла бы дефект. Сколько точек пришло без измерения,
  сообщается отдельной строкой (`:241-243`).
- Единица дописывается к значению только когда оно есть: `%` для процентов, код эквивалента для
  `amount`, ничего для `count` (`:193-201`).

---

## 5. Стили и типографика

### 5.1 Правило

Панель **не заводит собственных цветов, шрифтов и размеров**. Всё берётся из существующих
примитивов `ds-*` и токенов `--ds-*`; собственные scoped-стили компонентов описывают только
раскладку (flex, gap через `--ds-space-*`, переносы). Прежняя редакция §5 задавала сырой CSS с
литералами (`#1e293b`, `#334155`, `#ef4444`, `#f59e0b`, `#22c55e`, пиксельные кегли) — это нарушает
`simulator-ui/v2/src/ui-kit/AI-AGENT-GUIDE.md`, и хардкод **удалён, а не перенесён в компоненты**.

### 5.2 Соответствие: что чем выражено

| Элемент | Примитив / токен | Где |
|---|---|---|
| Корпус панели | `ds-panel`, `ds-ov-item`, `ds-ov-surface` | `RealMetricsPanel.vue:88` |
| Шапка / тело | `ds-panel__header`, `ds-panel__body` | `:93`, `:98` |
| Подпись KPI | `ds-section-label` | `MetricsKpiCard.vue:206` |
| Значение KPI | `ds-value ds-mono` | `:208` |
| Единица, приглушённый текст | `ds-muted` | `:209` |
| Спарклайн | `stroke`/`fill: var(--ds-accent)` | `:273-281` |
| Пояснение под карточкой | `ds-help` | `:240-243` |
| Строка bottleneck'а | `ds-subpanel` | `BottlenecksList.vue:64` |
| Кнопка Focus | `ds-btn ds-btn--ghost ds-btn--sm` | `:78-80` |
| Плашка `unavailable` | `ds-alert ds-alert--info` | `RealMetricsPanel.vue:113` |
| Плашка `error` | `ds-alert ds-alert--err` | `:102` |
| Геометрия дока | `--ds-hud-stack-height`, `--ds-hud-bottom-stack-height`, `--ds-ov-inset`, `--ds-ov-panel-maxw`, `--ds-z-panel` | `overlaySurfaceCatalog.ts:324-330` |

### 5.3 Пороги серьёзности bottleneck'а

Пороги остаются продуктовым решением, цвета — нет: `score >= 0.6` → `ds-badge--err`,
`score >= 0.3` → `ds-badge--warn`, ниже → `ds-badge--ok`, нечисловой `score` → `ds-badge--info`
(`BottlenecksList.vue:41-50`). Модификатор выбирает тему; тема выбирает цвет.

---

## 6. Что потребовало доработки

> Раздел — исходный план работ, сохранённый как карта поверхности. На 2026-08-21 всё перечисленное
> построено (программа 007, T700–T705); ссылки на файлы обновлены до фактических.

### 6.1 Backend (минимально)

| Задача | Файл | Сложность |
|--------|------|-----------|
| API endpoint `/runs/{run_id}/metrics` | `app/api/v1/simulator.py:2585` | ✅ Done |
| API endpoint `/runs/{run_id}/bottlenecks` | `app/api/v1/simulator.py:2604` | ✅ Done |
| Данные уже пишутся в DB | `storage.py:write_tick_metrics` | ✅ Done |
| Данные уже пишутся в DB | `storage.py:write_tick_bottlenecks` | ✅ Done |

**Примечание:** Schemas `MetricsResponse` и `BottlenecksResponse` уже определены в `schemas/simulator.py`.

### 6.2 Frontend

| Задача | Файл | Сложность |
|--------|------|-----------|
| `RealMetricsPanel.vue` | `components/RealMetricsPanel.vue` | ✅ Done |
| `MetricsKpiCard.vue` | `components/MetricsKpiCard.vue` | ✅ Done |
| `BottlenecksList.vue` | `components/BottlenecksList.vue` | ✅ Done |
| `useMetricsPolling.ts` | `composables/useMetricsPolling.ts` | ✅ Done |
| API клиент `getMetrics()` / `getBottlenecks()` через `simulatorContractJson` | `api/simulatorApi.ts:160-195` | ✅ Done |
| Декодеры ответов (в т.ч. `v: string \| null`) | `api/simulatorContracts.ts:590-660` | ✅ Done |
| Регистрация overlay-поверхности + док | `ui-kit/overlaySurfaceCatalog.ts`, `SimulatorAppRoot.vue` | ✅ Done |
| Toggle button | `BottomBar.vue:208-216` | ✅ Done |
| Сохранение видимости панели (`geo.sim.v2.analytics.open`) | `composables/usePersistedSimulatorPrefs.ts:218-225` | ✅ Done |
| Focus camera на edge | `useCamera.ts` (две точки) + `useAppViewWiring.ts` (id → точки) | ✅ Done |

### 6.3 НЕ ТРЕБУЕТСЯ менять

- `vizMapping.ts` — используем существующие цвета
- `nodePainter.ts` — логика отрисовки узлов
- `forceLayout.ts` — алгоритм layout (уже исправлен spacing)
- `fxRenderer.ts` — эффекты анимации
- ~~Backend schema — все типы уже определены~~ — **оценка не подтвердилась.** Схему пришлось менять:
  `MetricPoint.v` переведён с `float` на nullable decimal string (T715), а `MetricSeriesKey`
  доведён до семи значений (T713). Обе правки затронули и канон `api/openapi.yaml`.

---

## 7. API Endpoints (реализация)

### 7.1 GET /simulator/runs/{run_id}/metrics

`equivalent`, `from_ms`, `to_ms`, `step_ms` — **все обязательные, без значений по умолчанию**.
Клиент обязан посчитать окно сам; `step_ms >= 1`, число точек `(to_ms - from_ms) / step_ms + 1`
ограничено 2000 (`metrics_bottlenecks.py:165-167`).

```python
# app/api/v1/simulator.py:2585
@router.get("/runs/{run_id}/metrics", response_model=MetricsResponse)
async def metrics_for_run(
    run_id: str,
    equivalent: str = Query(...),
    from_ms: int = Query(..., ge=0),
    to_ms: int = Query(..., ge=0),
    step_ms: int = Query(..., ge=1),
    actor: deps.SimulatorActor = Depends(deps.require_simulator_actor),
):
    ...
```

Реальный режим читает только персистированные точки и ресемплит их carry-forward'ом от первого
измерения; при отказе хранилища — 503, не синтетика (см. §2.1). Синтетическая ветка достижима
только для `run.mode != "real"`.

### 7.2 GET /simulator/runs/{run_id}/bottlenecks

```python
# app/api/v1/simulator.py:2604
@router.get("/runs/{run_id}/bottlenecks", response_model=BottlenecksResponse)
async def bottlenecks_for_run(
    run_id: str,
    equivalent: str = Query(...),
    limit: int = Query(20, ge=1, le=200),
    min_score: Optional[float] = Query(None, ge=0.0, le=1.0),
    actor: deps.SimulatorActor = Depends(deps.require_simulator_actor),
):
    ...
```

Панель запрашивает `limit=10` (`useMetricsPolling.ts:32`) — короткий список, а не весь бэклог.

---

## 8. Acceptance Criteria

1. ✅ Панель показывает пришедшие серии из семи объявленных, в порядке §2.1, и не индексирует
   вслепую, когда серий меньше
2. ✅ Панель показывает список bottlenecks с score и reason
3. ✅ Клик на bottleneck центрирует камеру на edge; ребро, которого нет в текущем снапшоте, не
   двигает камеру и не бросает исключение
4. ✅ Edge подсвечивается через существующий механизм `activeEdges` — выполнено 2026-08-21. `SimulatorAppRoot.vue:857-879`: подсветка ставится **только после успешного наведения** (`focusOnEdge` вернул `true`), потому что подсветить ребро, которого нет в снапшоте, значило бы соврать пользователю вторым способом. TTL 3000 мс — при `ACTIVE_EDGE_FADE_MS = 1200` это ~1800 мс полной яркости плюс видимое затухание; 1500 оставили бы 300 мс, 5200 горели бы ещё при клике по следующей строке.
5. ✅ Данные обновляются каждые 5 секунд, пока run=running **и панель открыта**; скрытая панель не
   опрашивает
6. ✅ Панель скрывается/показывается кнопкой, состояние переживает перезагрузку
7. ✅ Стили — только примитивы `ds-*` и токены `--ds-*`, ни одного литерального цвета
8. ✅ Не меняется логика визуализации узлов/рёбер
9. ✅ Три состояния различимы: `ready`, `unavailable` (нейтрально, с причиной), `error` (как
   отказ). `unavailable` не выглядит ошибкой
10. ✅ `null` в ряду рисуется разрывом, `"0.00000000"` — точкой на нуле; заголовок карточки при
    последнем `null` показывает прочерк
11. ✅ `v` нигде не парсится в число; значение вне контракта помечается отдельно, а не выдаётся за
    разрыв
