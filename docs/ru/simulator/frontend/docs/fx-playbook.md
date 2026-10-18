# GEO Simulator Frontend — FX playbook (эффекты, правила композиции)

Статус: Draft
Область: simulator
Последнее обновление: 2026-10-18 (программа 039, срез C: движение — раздел 4.1 [visual-language.md](visual-language.md))

Цель: минимизировать регрессии вида «двойная анимация», «сброс overlay съел искры», «цвет внезапно стал cyan», и ускорить разработку новых эффектов.

## 0) Архитектура FX

FX — это отдельный overlay canvas поверх базового графа.

- Базовый граф: постоянно рисует узлы/рёбра и их LOD.
- FX overlay: рисует краткоживущие эффекты (частицы/пульсы/вспышки).

См. общий контекст: [archive/GEO-visual-demo-fast-mock.md](../../../archive/simulator/frontend/docs/archive/GEO-visual-demo-fast-mock.md).

## 1) Примитивы FX (что у нас есть)

Код: `simulator-ui/v2/src/render/fxRenderer.ts`

### 1.1 Sparks (`FxSpark`)

Назначение: «полет» вдоль ребра (tx или clearing step).

Варианты:
- `kind: 'beam'` — луч + яркий пакет + точка головы (платёж и искра клиринга). Поля `moveMs` (сколько движется голова) и `motion` (`linear` / `arrive`) задаёт расписание `composables/realFx/txRoutePlan.ts`; `ttlMs - moveMs` — угасание после прихода (только последняя нога).
- ~~`kind: 'comet'`~~ — «хвостатая» искра с колебанием; удалена 2026-10-18 (039, срез C): ни один путь её не порождал.

Важно: `beam` уже включает визуальный glow ребра.

### 1.2 Edge Pulses (`FxEdgePulse`)

Назначение: показать «маршрут/цикл» без отдельной искры-головы.

Когда использовать:
- подсветить путь (без эффекта “летящей транзакции”).

### 1.3 Node Bursts (`FxNodeBurst`)

Назначение: свечение контура узла, который проходит луч или искра клиринга (`kind: 'tx-impact'` — единственный вид с 039 C; `glow` и `clearing` с композицией `screen` удалены, их никто не порождал).

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

### 3.1 Платёж (как сделано, 039 C)

1) `spawnNodeBursts(sender, 'tx-impact')` — сразу;
2) ноги по расписанию `planTxRoute` (одна скорость, не меньше 450 мс на ногу, последняя с замедлением и угасанием): `spawnSparks(edge, kind:'beam', moveMs, motion)`; в начале каждой ноги после первой — `spawnNodeBursts(посредник, 'tx-impact')`;
3) в момент прихода (`routeArrivalMs`): `spawnNodeBursts(receiver, 'tx-impact')` и подпись суммы получателя.

### 3.2 Клиринг (как сделано, 039 C)

- Одна искра обходит каждый цикл за 1.2 с: на каждом ребре `spawnSparks(kind:'beam', оранжевый, motion:'linear')`, на уходе из узла — `spawnNodeBursts('tx-impact')`;
- после обхода: `spawnEdgePulses` по линиям циклов (1.6 с), подсветка цикла на базовом графе, подпись суммы.
- Полноэкранной вспышки нет (удалена 039 C).

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

### 6.1 Clearing node bursts (историческое, до 2026-10-18)

До среза C программы 039 клиринг из Interact (`onClearingDone` в [`useSimulatorApp.ts`](../../../../../simulator-ui/v2/src/composables/useSimulatorApp.ts)) зажигал вместе с пульсами всех линий до 40 узлов разом (`kind: 'clearing'`, bloom и ударная волна, 2.8 с, композиция `screen`) и полноэкранную вспышку. С 2026-10-18 клиринг из Interact и из SSE идут одним путём `runClearingFx` — раздел 3.2.

### 6.2 Picking dimming (затемнение недоступных узлов)

При выборе отправителя/получателя в Interact Mode недоступные узлы затемняются на уровне base graph (не FX):

- **Параметр:** `dimmedNodeIds?: Set<string>` в [`drawBaseGraph()`](../../../../../simulator-ui/v2/src/render/baseGraph.ts)
- **Эффект:** узлы, входящие в `dimmedNodeIds`, рендерятся с `globalAlpha = 0.25`
- **Pipeline:** прокинуто через [`useRenderLoop.ts`](../../../../../simulator-ui/v2/src/composables/useRenderLoop.ts) → [`useAppRenderLoop.ts`](../../../../../simulator-ui/v2/src/composables/useAppRenderLoop.ts) → [`useAppFxOverlays.ts`](../../../../../simulator-ui/v2/src/composables/useAppFxOverlays.ts) → [`useSimulatorApp.ts`](../../../../../simulator-ui/v2/src/composables/useSimulatorApp.ts)
- **Источник данных:** `availableTargetIds` computed в `useInteractMode` — набор доступных узлов; все остальные попадают в `dimmedNodeIds`

> **Важно:** Это не FX-эффект, а параметр base graph рендера. При выходе из picking-фазы `dimmedNodeIds` сбрасывается (пустой Set), и все узлы возвращаются к нормальной отрисовке.
