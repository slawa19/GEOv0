# GEO Simulator Frontend — FX playbook (эффекты, правила композиции)

Статус: Draft
Область: simulator
Последнее обновление: 2026-01-23

Цель: минимизировать регрессии вида «двойная анимация», «сброс overlay съел искры», «цвет внезапно стал cyan», и ускорить разработку новых эффектов.

## 0) Архитектура FX

FX — это отдельный overlay canvas поверх базового графа.

- Базовый граф: постоянно рисует узлы/рёбра и их LOD.
- FX overlay: рисует краткоживущие эффекты (частицы/пульсы/вспышки).

См. общий контекст: [specs/GEO-visual-demo-fast-mock.md](specs/GEO-visual-demo-fast-mock.md).

## 1) Примитивы FX (что у нас есть)

Код: `simulator-ui/v2/src/render/fxRenderer.ts`

### 1.1 Sparks (`FxSpark`)

Назначение: «полет» вдоль ребра (tx или clearing step).

Варианты:
- `kind: 'comet'` — “хвостатая” искра (подходит для простых Tx).
- `kind: 'beam'` — луч + яркий пакет + star head (подходит для “прототипной” Single Tx и клиринга).

Важно: `beam` уже включает визуальный glow ребра.

### 1.2 Edge Pulses (`FxEdgePulse`)

Назначение: показать «маршрут/цикл» без отдельной искры-головы.

Когда использовать:
- подсветить путь (без эффекта “летящей транзакции”).

### 1.3 Node Bursts (`FxNodeBurst`)

Назначение: вспышка/свечение на узле (impact/selection/clearing).

Типичные кейсы:
- impact на узле-получателе при прилёте искры;
- локальное soft-glow без full-screen flash.

## 2) Главные правила (DO / DON’T)

### 2.1 Не делать «двойной проход»

- DON’T: `spawnEdgePulses(...)` + `spawnSparks(kind:'beam')` на том же ребре в одном и том же шаге.
  - Визуально читается как 2 разных движения.
- DO:
  - либо `beam`-искра (движение + glow),
  - либо edge-pulse (маршрут без искры).

### 2.2 Цветовая конвенция

- Single Tx: cyan (#22d3ee)
- Clearing: gold (#fbbf24)

Если вдруг clearing становится cyan — это обычно признак того, что включили подсветку базового графа (activeEdges) вместо FX.

### 2.3 Сброс overlay и тайминги

- FX живут по `ttlMs`/`durationMs` и очищаются компактацией массивов на каждом кадре.
- При “run sequence guards” (защита от устаревших таймеров) важно, чтобы сброс FX не выполнялся слишком рано и не обрывал текущие искры.

## 3) Рекомендованные паттерны для сценариев

### 3.1 Single Tx (рекомендуемая последовательность)

1) (опционально) `spawnNodeBursts(source, 'glow')`
2) `spawnSparks(edge, kind:'beam')`
3) по окончании `ttlMs`: `spawnNodeBursts(target, 'tx-impact' или 'glow')`

### 3.2 Clearing (рекомендуемая последовательность)

- Сериализация шагов вдоль цикла:
  - на каждом ребре: `spawnSparks(kind:'beam', gold)`
  - на приходе: `spawnNodeBursts('glow', gold)`
- DON’T: full-screen flash (если он нужен для отдельного режима — делать явно и настраиваемо).

## 4) Как добавлять новый эффект без регрессий

1) Определить: это движение по ребру (Spark) или “маршрут” (EdgePulse) или “локальный акцент” (NodeBurst).
2) Проверить, что не включаете одновременно два примитива, читающихся как один и тот же смысл.
3) Сделать параметры детерминированными (seeded), без `Math.random()`.
4) Добавить/обновить фикстуру события так, чтобы эффект воспроизводился (и не зависел от состояния предыдущих сцен).

## 5) Где смотреть конфликты

- Цвета/ключи: `simulator-ui/v2/src/vizMapping.ts`
- Базовые активные рёбра: `simulator-ui/v2/src/render/baseGraph.ts` (могут быть намеренно "electric cyan").
- FX рендер: `simulator-ui/v2/src/render/fxRenderer.ts`

## 6) Interact Mode — дополнительные эффекты

### 6.1 Clearing node bursts

После завершения клиринга в Interact Mode, помимо стандартных `spawnEdgePulses()` (gold pulse по рёбрам циклов), вызывается `spawnNodeBursts()` для узлов, участвующих в найденных циклах:

- **Вызов:** `spawnNodeBursts({ kind: 'clearing', durationMs: 2800, color: VIZ_MAPPING.fx.clearing_debt })`
- **Где:** callback `onClearingDone` в [`useSimulatorApp.ts`](../../../../../simulator-ui/v2/src/composables/useSimulatorApp.ts)
- **Ограничение:** до 40 узлов (для производительности)
- **Визуально:** bloom + shockwave ring на каждом узле цикла, gold-цвет, длительность 2.8 секунды

> **Правило:** `spawnNodeBursts(clearing)` вызывается **после** `spawnEdgePulses()` — это дополняющий эффект, а не заменяющий. Edge pulses подсвечивают маршрут цикла, node bursts акцентируют участников.

### 6.2 Picking dimming (затемнение недоступных узлов)

При выборе отправителя/получателя в Interact Mode недоступные узлы затемняются на уровне base graph (не FX):

- **Параметр:** `dimmedNodeIds?: Set<string>` в [`drawBaseGraph()`](../../../../../simulator-ui/v2/src/render/baseGraph.ts)
- **Эффект:** узлы, входящие в `dimmedNodeIds`, рендерятся с `globalAlpha = 0.25`
- **Pipeline:** прокинуто через [`useRenderLoop.ts`](../../../../../simulator-ui/v2/src/composables/useRenderLoop.ts) → [`useAppRenderLoop.ts`](../../../../../simulator-ui/v2/src/composables/useAppRenderLoop.ts) → [`useAppFxAndRender.ts`](../../../../../simulator-ui/v2/src/composables/useAppFxAndRender.ts) → [`useSimulatorApp.ts`](../../../../../simulator-ui/v2/src/composables/useSimulatorApp.ts)
- **Источник данных:** `availableTargetIds` computed в `useInteractMode` — набор доступных узлов; все остальные попадают в `dimmedNodeIds`

> **Важно:** Это не FX-эффект, а параметр base graph рендера. При выходе из picking-фазы `dimmedNodeIds` сбрасывается (пустой Set), и все узлы возвращаются к нормальной отрисовке.
