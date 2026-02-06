# Аудит визуальных элементов рендера Simulator UI

> **Дата актуализации:** 2026-02-06  
> **Источники:** текущий код после всех рефакторов interaction quality v2

---

## Обзор архитектуры рендера

Рендер Simulator UI использует **двухслойную canvas-архитектуру** с DOM-оверлеем:

| Слой | Назначение | Canvas |
|------|-----------|--------|
| **Base Graph** | Ноды, линки, selection/active glow | Основной `<canvas>` ([`baseGraph.ts`](simulator-ui/v2/src/render/baseGraph.ts)) |
| **FX Overlay** | Искры, пульсы, взрывы, flash-оверлей | Отдельный `<canvas>` поверх основного ([`fxRenderer.ts`](simulator-ui/v2/src/render/fxRenderer.ts)) |
| **DOM Labels** | Текстовые метки нод, тултипы рёбер | HTML-элементы поверх canvas (LabelsOverlayLayers) |

### Render Loop ([`useRenderLoop.ts`](simulator-ui/v2/src/composables/useRenderLoop.ts))

Цикл рендера работает в трёх режимах:

1. **Active** — `requestAnimationFrame` на каждый кадр (60fps target)
2. **Idle** — throttled до [`idleFps=4`](simulator-ui/v2/src/composables/useRenderLoop.ts:198) через `setTimeout`
3. **Deep Idle** — полная остановка после [`DEEP_IDLE_DELAY_MS=3000`](simulator-ui/v2/src/composables/useRenderLoop.ts:151) без активности

Переходы:
- Active → Idle: после [`holdActiveMs=250`](simulator-ui/v2/src/composables/useRenderLoop.ts:168) без анимаций
- Idle → Deep Idle: после `3000ms` без какой-либо активности
- Deep Idle → Active: вызов [`wakeUp()`](simulator-ui/v2/src/composables/useRenderLoop.ts:768)

### Interaction Quality ([`interactionHold.ts`](simulator-ui/v2/src/composables/interactionHold.ts))

Система плавного управления качеством при взаимодействии пользователя:

- [`markInteraction()`](simulator-ui/v2/src/composables/interactionHold.ts:143) устанавливает дедлайн `holdMs=250ms`
- [`getIntensity()`](simulator-ui/v2/src/composables/interactionHold.ts:114) возвращает `0.0–1.0` с плавным easing
- `intensity` влияет на `blurK`: [`blurK = baseBlurK * (1 - intensity)`](simulator-ui/v2/src/render/nodePainter.ts:144)
- При `intensity=1.0` → `blurK=0` → все shadowBlur отключены (дешёвый рендер)
- При `intensity=0.0` → `blurK=baseBlurK` → полное качество

---

## Система качества (Quality Levels)

### Три уровня качества

| Уровень | `baseBlurK` (nodes) | `baseBlurK` (base/fx) | Body fill | Градиенты FX | DPR clamp |
|---------|---------------------|----------------------|-----------|--------------|-----------|
| **High** | `1.0` ([`nodePainter.ts:142`](simulator-ui/v2/src/render/nodePainter.ts:142)) | `1.0` ([`baseGraph.ts:66`](simulator-ui/v2/src/render/baseGraph.ts:66)) | `createLinearGradient` ([`:243`](simulator-ui/v2/src/render/nodePainter.ts:243)) | Да (`allowGradients`) | `2.0` |
| **Med** | `0.75` | `0` (baseGraph), `0.75` (FX) | Solid `withAlpha(fill, 0.42)` | Нет при `intensity≥0.5` | `1.5` |
| **Low** | `0` | `0` | Solid | Нет | `1.0` |

### Interaction Intensity (0.0–1.0)

Фазовая машина [`createInteractionHold()`](simulator-ui/v2/src/composables/interactionHold.ts:55):

| Фаза | Intensity | Длительность |
|------|-----------|-------------|
| `idle` | `0.0` | — |
| `ramping-up` | `0→1.0` | [`easeInMs=100`](simulator-ui/v2/src/composables/interactionHold.ts:57) |
| `holding` | `1.0` | пока `markInteraction()` вызывается |
| `delaying` | фиксированная (последнее значение) | [`easeOutDelayMs=200`](simulator-ui/v2/src/composables/interactionHold.ts:58) |
| `ramping-down` | `value→0.0` | [`easeOutMs=150`](simulator-ui/v2/src/composables/interactionHold.ts:59) |

### Adaptive Quality / DPR Degradation

Адаптивная система в [`updateAdaptivePerf()`](simulator-ui/v2/src/composables/useRenderLoop.ts:546):

- Семплирование FPS каждые [`sampleWindowMs=900ms`](simulator-ui/v2/src/composables/useRenderLoop.ts:156)
- Требуется [`downgradeStreak=2`](simulator-ui/v2/src/composables/useRenderLoop.ts:170) последовательных плохих семплов для понижения
- Требуется [`upgradeStreak=3`](simulator-ui/v2/src/composables/useRenderLoop.ts:173) хороших семплов для повышения
- **Warmup** [`warmupMs=2000`](simulator-ui/v2/src/composables/useRenderLoop.ts:165): после старта активности даунгрейд запрещён (но `wakeUp('user')` пропускает warmup — [`строка 590`](simulator-ui/v2/src/composables/useRenderLoop.ts:590))

Пороги FPS ([строки 180–191](simulator-ui/v2/src/composables/useRenderLoop.ts:180)):

| Метрика | FPS | Действие |
|---------|-----|----------|
| `criticalLow` | `<18` | → Low |
| `lowFromHigh` | `<26` | High → Low |
| `medFromHigh` | `<34` | High → Med |
| `lowFromMed` | `<24` | Med → Low |
| `dprCritical` | `<20` | DPR → 1.0 |
| `dprModerate` | `<28` | DPR → 1.25 |
| `upgradeHigh` | `≥48` | → High |
| `upgradeMed` | `≥42` | → Med |

---

## Триггеры взаимодействий

### Таблица событий → действия

Источник: [`useAppCanvasInteractionsWiring.ts`](simulator-ui/v2/src/composables/useAppCanvasInteractionsWiring.ts)

| UI Event | `mark()` | `wakeUp()` | Комментарий |
|----------|----------|-----------|-------------|
| **click** | ❌ нет ([`:42`](simulator-ui/v2/src/composables/useAppCanvasInteractionsWiring.ts:42)) | `'user'` | Мгновенное действие, не нужно снижать качество |
| **dblclick** | ❌ нет ([`:47`](simulator-ui/v2/src/composables/useAppCanvasInteractionsWiring.ts:47)) | `'user'` | Аналогично click |
| **pointerdown** | ❌ нет ([`:51`](simulator-ui/v2/src/composables/useAppCanvasInteractionsWiring.ts:51)) | `'user'` | Начало взаимодействия, ещё не continuous |
| **pointermove** (hover) | ❌ нет ([`:59`](simulator-ui/v2/src/composables/useAppCanvasInteractionsWiring.ts:59)) | `'user'` | `ev.buttons === 0` → без `mark()` |
| **pointermove** (drag) | ✅ `mark()` ([`:59`](simulator-ui/v2/src/composables/useAppCanvasInteractionsWiring.ts:59)) | `'user'` | `ev.buttons !== 0` → `mark()` без instant |
| **pointerup** | ❌ нет ([`:63`](simulator-ui/v2/src/composables/useAppCanvasInteractionsWiring.ts:63)) | `'user'` | Конец взаимодействия, hold timer обрабатывает |
| **wheel** | ✅ `mark({instant:true})` ([`:69`](simulator-ui/v2/src/composables/useAppCanvasInteractionsWiring.ts:69)) | через camera.onCameraChanged | Instant для мгновенного `intensity=1` |

### Что происходит при каждом взаимодействии

- **Hover**: `wakeUp('user')` будит loop из deep idle; edge hover detection; **качество НЕ снижается**
- **Click**: `wakeUp('user')` + выбор ноды/снятие выделения; отрисовка Selection Glow; **без mark()**
- **Drag**: `mark()` на каждый pointermove → `intensity` нарастает → `blurK` уменьшается → blur отключается; `dragMode=true` → ноды рисуются упрощённо
- **Wheel**: `mark({instant:true})` → мгновенно `intensity=1, blurK=0`; камера масштабируется
- **Dblclick**: zoom-to-fit или открытие node card; **без mark()**

---

## Полная таблица визуальных элементов

### Node Painting ([`nodePainter.ts`](simulator-ui/v2/src/render/nodePainter.ts))

#### 1. Drag Fast-Path (fill + stroke + icon + badge)

- **Файл:** [`nodePainter.ts:164–191`](simulator-ui/v2/src/render/nodePainter.ts:164)
- **Описание:** Упрощённая отрисовка ноды при `dragMode=true`. Полупрозрачная заливка (`alpha=0.45`), тонкий белый stroke, иконка и бейдж.
- **Условия видимости:** Только при `opts.dragMode === true`
- **Поведение при idle:** Не используется
- **Поведение при drag:** Единственный путь отрисовки; все blur/gradient пропущены
- **Стоимость:** ⚡ Очень низкая — `arc`/`roundedRectPath` + `fill` + `stroke` + иконка (несколько arc/rect)
- **Визуально:** Полупрозрачная форма ноды с белой обводкой, иконкой внутри и опциональным бейджом

#### 2. Bloom Underlay (shadowBlur vs glowSprite)

- **Файл:** [`nodePainter.ts:198–235`](simulator-ui/v2/src/render/nodePainter.ts:198)
- **Описание:** Мягкое свечение под нодой. «Голографический» glow в режиме `screen` blend.
- **Условия видимости:**
  - `softwareMode=true` → всегда через [`drawGlowSprite()`](simulator-ui/v2/src/render/glowSprites.ts:126) (все quality levels)
  - `softwareMode=false` → только при `blurK > 0` (т.е. `quality !== 'low'` И `intensity < 1.0`)
- **Поведение при idle:** Полный blur (`blurK=1`), `shadowBlur = r * 1.5`
- **Поведение при drag:** Пропущено (drag fast-path return на [строке 191](simulator-ui/v2/src/render/nodePainter.ts:191))
- **Поведение при wheel:** `blurK=0` → блок пропускается (`blurK > 0` false)
- **Стоимость:** 🔴 Высокая (shadowBlur) / 🟡 Средняя (glowSprite drawImage)
  - `shadowBlur` — GPU-intensive gaussian blur per-fill
  - `drawGlowSprite` — кэшированный canvas, один `drawImage`
- **Визуально:** Широкое мягкое свечение цвета ноды (screen blend), «ореол» вокруг формы

#### 3. Body Fill (gradient vs solid)

- **Файл:** [`nodePainter.ts:238–258`](simulator-ui/v2/src/render/nodePainter.ts:238)
- **Описание:** Основная заливка тела ноды. Полупрозрачное «стекло».
- **Условия видимости:** Всегда (не зависит от quality/intensity/drag — drag имеет свой path)
- **Поведение при idle (High):** `createLinearGradient` с двумя стопами (`0.55→0.25` alpha)
- **Поведение при idle (Med/Low):** Solid `withAlpha(fill, 0.42)`
- **Поведение при drag:** Свой path (см. Drag Fast-Path)
- **Стоимость:** 🟡 Средняя (gradient в High) / ⚡ Низкая (solid в Med/Low)
  - `createLinearGradient` + 2 addColorStop — умеренная CPU стоимость
  - Градиенты остаются даже при interaction (их отсутствие визуально заметно, [комментарий строка 241](simulator-ui/v2/src/render/nodePainter.ts:241))
- **Визуально:** Полупрозрачная цветная заливка формы ноды (круг или rounded-rect)

#### 4. Neon Rim — Outer Glow Stroke (shadowBlur)

- **Файл:** [`nodePainter.ts:262–297`](simulator-ui/v2/src/render/nodePainter.ts:262)
- **Описание:** Внешний светящийся обвод ноды. `screen` blend с `shadowBlur`.
- **Условия видимости:**
  - `softwareMode=true` → через [`drawGlowSprite(kind='rim')`](simulator-ui/v2/src/render/glowSprites.ts:126)
  - `softwareMode=false` → stroke с `shadowBlur` пропорциональным `blurK`
- **Поведение при idle:** `shadowBlur = max(px(2), r * 0.3) * blurK` — полное свечение
- **Поведение при drag:** Пропущено (drag fast-path)
- **Поведение при wheel:** `blurK=0` → `shadowBlur=0`, но stroke всё равно рисуется
- **Стоимость:** 🔴 Высокая (shadowBlur) / 🟡 Средняя (glowSprite)
- **Визуально:** Неоновый обвод вокруг ноды, цвет заливки с alpha 0.6, мягкий glow

#### 5. Neon Rim — Outer Stroke (без blur)

- **Файл:** [`nodePainter.ts:284–296`](simulator-ui/v2/src/render/nodePainter.ts:284)
- **Описание:** Тот же stroke что и п.4, но при `blurK=0` (`intensity=1` или `quality='low'`) — рисуется как обычный stroke без тени.
- **Условия видимости:** Всегда в non-software mode (shadowBlur просто =0)
- **Стоимость:** ⚡ Низкая — обычный `stroke()` без blur
- **Визуально:** Тонкий цветной обвод без мягкого свечения

#### 6. Neon Rim — Inner White Core

- **Файл:** [`nodePainter.ts:299–311`](simulator-ui/v2/src/render/nodePainter.ts:299)
- **Описание:** Тонкий яркий белый обвод поверх цветного. `shadowBlur=0` всегда.
- **Условия видимости:** Всегда (кроме drag fast-path)
- **Поведение при idle/drag/wheel:** Одинаковое — всегда рисуется
- **Стоимость:** ⚡ Низкая — `stroke()` без blur
- **Визуально:** Острый белый контур (`alpha=0.9`, `lineWidth = max(px(1), r*0.05)`)

#### 7. Icons (drawNodeIcon — person/building silhouette)

- **Файл:** [`nodePainter.ts:18–77`](simulator-ui/v2/src/render/nodePainter.ts:18)
- **Описание:** Иконка внутри ноды: силуэт человека (circle) или здания (rounded-rect).
- **Условия видимости:** Всегда — вызывается и в drag path ([`:186`](simulator-ui/v2/src/render/nodePainter.ts:186)), и в обычном ([`:315`](simulator-ui/v2/src/render/nodePainter.ts:315))
- **Стоимость:** ⚡ Очень низкая — несколько `arc`/`quadraticCurveTo`/`rect` + `fill`. Комментарий: «Cost ≈ a few arc/rect calls, no blur/gradient» ([`:16`](simulator-ui/v2/src/render/nodePainter.ts:16))
- **Визуально:**
  - Человек: голова (arc) + тело (quadraticCurveTo), alpha=0.95
  - Здание: прямоугольник + крыша + окна (destination-out cutouts)

#### 8. Badge Pip (drawNodeBadge)

- **Файл:** [`nodePainter.ts:82–97`](simulator-ui/v2/src/render/nodePainter.ts:82)
- **Описание:** Маленький белый кружок в правом верхнем углу ноды. `lighter` blend.
- **Условия видимости:** Только если `node.viz_badge_key !== undefined && !== null` ([`:187`](simulator-ui/v2/src/render/nodePainter.ts:187), [`:318`](simulator-ui/v2/src/render/nodePainter.ts:318))
- **Стоимость:** ⚡ Минимальная — один `arc` + `fill`. Комментарий: «Cost ≈ one arc + fill — negligible» ([`:80`](simulator-ui/v2/src/render/nodePainter.ts:80))
- **Визуально:** Белый pip (`alpha=0.85`) в позиции `(cx + r*0.72, cy - r*0.72)`

---

### Base Graph ([`baseGraph.ts`](simulator-ui/v2/src/render/baseGraph.ts))

#### 1. Links Base Pass

- **Файл:** [`baseGraph.ts:76–104`](simulator-ui/v2/src/render/baseGraph.ts:76)
- **Описание:** Отрисовка всех линков по семантическим `viz_*` ключам. Без focus/active override.
- **Условия видимости:** Всегда; при `linkLod='focus'` — только active + incident к выбранной ноде ([`:77–81`](simulator-ui/v2/src/render/baseGraph.ts:77))
- **Поведение при drag:** `dragMode=true` → alpha увеличена (`max(0.22, baseAlpha*2.4)`), width boosted ([`:95–96`](simulator-ui/v2/src/render/baseGraph.ts:95))
- **Стоимость:** ⚡ Низкая — `moveTo` + `lineTo` + `stroke` per link. O(links).
- **Визуально:** Тонкие цветные линии между нодами

#### 2. Links Overlay (Focus/Active Highlight)

- **Файл:** [`baseGraph.ts:107–153`](simulator-ui/v2/src/render/baseGraph.ts:107)
- **Описание:** Второй проход по линкам — подсветка фокусных (инцидентных выбранной ноде) и активных (участвующих в транзакции).
- **Условия видимости:** `!dragMode && (selectedNodeId || activeEdges.size > 0)` ([`:107`](simulator-ui/v2/src/render/baseGraph.ts:107))
- **Поведение при drag:** Полностью пропускается
- **Стоимость:** ⚡ Низкая — те же `moveTo/lineTo/stroke`, но только для подмножества линков
- **Визуально:**
  - Focus: `alpha * 3.0`, width ≥ `thin` ([`:130–131`](simulator-ui/v2/src/render/baseGraph.ts:130))
  - Active: цвет `mapping.fx.tx_spark.trail`, `alpha * 4.0`, width ≥ `highlight` ([`:143–144`](simulator-ui/v2/src/render/baseGraph.ts:143))

#### 3. Selection Glow (shadowBlur + fallback)

- **Файл:** [`baseGraph.ts:161–223`](simulator-ui/v2/src/render/baseGraph.ts:161)
- **Описание:** Свечение вокруг выбранной ноды. Двухпроходное: широкий blur + узкий core.
- **Условия видимости:** `isSelected && !dragMode` ([`:161`](simulator-ui/v2/src/render/baseGraph.ts:161))
- **Поведение при High + blurK>0.1:**
  - `screen` blend, `shadowColor = glow`, `shadowBlur = r * 1.2 * blurK` ([`:180`](simulator-ui/v2/src/render/baseGraph.ts:180))
  - Трюк: `strokeStyle='#000000'` (невидимый в screen), только shadow виден ([`:181`](simulator-ui/v2/src/render/baseGraph.ts:181))
  - Второй проход: `shadowBlur = r * 0.4 * blurK` ([`:198`](simulator-ui/v2/src/render/baseGraph.ts:198))
- **Fallback (Med/Low или blurK≤0.1):**
  - `source-over` blend, цветной stroke без blur ([`:203–222`](simulator-ui/v2/src/render/baseGraph.ts:203))
  - `strokeStyle = withAlpha(glow, 0.9)`, offset наружу
- **Стоимость:** 🔴 Высокая (High blur) / ⚡ Низкая (fallback stroke)
- **Визуально:** Яркий ореол цвета ноды вокруг выбранной ноды

#### 4. Active Node Glow (shadowBlur + fallback)

- **Файл:** [`baseGraph.ts:226–275`](simulator-ui/v2/src/render/baseGraph.ts:226)
- **Описание:** Свечение вокруг активных нод (clearing). Аналогично Selection Glow, но слабее и цвет `mapping.fx.clearing_debt`.
- **Условия видимости:** `isActiveNode && !isSelected && !dragMode` ([`:226`](simulator-ui/v2/src/render/baseGraph.ts:226))
- **Поведение при High + blurK>0.1:**
  - `shadowBlur = r * 0.55 * blurK`, `globalAlpha = 0.85` ([`:241–243`](simulator-ui/v2/src/render/baseGraph.ts:241))
- **Fallback:**
  - Stroke `withAlpha(glow, 0.65)` без blur ([`:258–262`](simulator-ui/v2/src/render/baseGraph.ts:258))
- **Стоимость:** 🔴 Высокая (blur) / ⚡ Низкая (fallback)
- **Визуально:** Более мягкий ореол чем selection, цвет clearing_debt

#### 5. Link Labels

- **Описание:** Текстовые метки рёбер — отрисовываются через DOM overlay (не canvas). Не присутствуют в [`baseGraph.ts`](simulator-ui/v2/src/render/baseGraph.ts) напрямую.
- **Стоимость:** DOM layout + paint — зависит от количества видимых меток

---

### FX Effects ([`fxRenderer.ts`](simulator-ui/v2/src/render/fxRenderer.ts))

Общие принципы FX рендера:
- FX **всегда рендерятся** (нет early return при interaction) — [`строка 387–389`](simulator-ui/v2/src/render/fxRenderer.ts:387)
- При `intensity≥0.5` → `allowGradients=false` ([`:371`](simulator-ui/v2/src/render/fxRenderer.ts:371)), используются solid colors
- `blurK` управляет shadowBlur аналогично нодам
- Composite mode: `lighter` для аддитивного свечения
- Per-frame кэш [`nodeOutlinePath2DCache`](simulator-ui/v2/src/render/fxRenderer.ts:53) очищается каждый кадр

#### 1. Spark Beam — Trail (shadowBlur + gradient)

- **Файл:** [`fxRenderer.ts:445–487`](simulator-ui/v2/src/render/fxRenderer.ts:445)
- **Описание:** Луч от «хвоста» до «головы» искры (beam стиль). Два прохода: halo (wide + blur) и core (thin + sharp).
- **Условия видимости:** `s.kind === 'beam'` ([`:417`](simulator-ui/v2/src/render/fxRenderer.ts:417))
- **Стоимость:**
  - 🔴 Halo: `shadowBlur = max(spx(10), th*18) * blurK` ([`:471`](simulator-ui/v2/src/render/fxRenderer.ts:471)) + `createLinearGradient` (если `allowGradients`)
  - ⚡ Core: `shadowBlur=0` ([`:479`](simulator-ui/v2/src/render/fxRenderer.ts:479)), тонкий stroke
- **Визуально:** Светящийся луч с градиентом от прозрачного к яркому, ограниченной длины (max 85% ребра), сужающийся к концу

#### 2. Spark Beam — Head

- **Файл:** [`fxRenderer.ts:489–524`](simulator-ui/v2/src/render/fxRenderer.ts:489)
- **Описание:** «Пакет» — яркий сегмент у головы + светящаяся точка.
- **Стоимость:**
  - 🔴 Segment: `shadowBlur = max(spx(12), th*20) * blurK` ([`:505`](simulator-ui/v2/src/render/fxRenderer.ts:505)) + gradient
  - 🔴 Dot: `shadowBlur = max(spx(14), r*5) * blurK` ([`:517`](simulator-ui/v2/src/render/fxRenderer.ts:517)) + `arc` fill
- **Визуально:** Яркая движущаяся точка с коротким ярким хвостом

#### 3. Spark Comet Trail

- **Файл:** [`fxRenderer.ts:530–634`](simulator-ui/v2/src/render/fxRenderer.ts:530)
- **Описание:** «Кометный» стиль искры с колебаниями (wobble) и шлейфом.
- **Стоимость:**
  - 🔴 Glow pass: `shadowBlur = max(spx(6), th*10) * blurK` ([`:575`](simulator-ui/v2/src/render/fxRenderer.ts:575)) + gradient
  - ⚡ Core pass: `shadowBlur=0` ([`:586`](simulator-ui/v2/src/render/fxRenderer.ts:586))
  - 🔴 Head: `shadowBlur = max(spx(10), r*6) * blurK` ([`:609`](simulator-ui/v2/src/render/fxRenderer.ts:609))
  - ⚡ Embers: 3× `arc` fill без blur ([`:622`](simulator-ui/v2/src/render/fxRenderer.ts:622))
- **Визуально:** Волнистый хвост + яркая голова + мелкие «искры» позади

#### 4. Edge Pulse

- **Файл:** [`fxRenderer.ts:641–718`](simulator-ui/v2/src/render/fxRenderer.ts:641)
- **Описание:** Мягкий пульс, бегущий по ребру. Для подсветки cyclic path.
- **Стоимость:**
  - ⚡ Фоновая линия: `globalAlpha = alpha * 0.10`, обычный stroke ([`:682–688`](simulator-ui/v2/src/render/fxRenderer.ts:682))
  - 🔴 Пульс: `shadowBlur = max(spx(10), th*14) * blurK` ([`:702`](simulator-ui/v2/src/render/fxRenderer.ts:702)) + gradient
  - ⚡ Head dot: `arc` fill без blur ([`:711`](simulator-ui/v2/src/render/fxRenderer.ts:711))
- **Визуально:** Тонкая фоновая линия + яркий движущийся сегмент + точка-голова

#### 5. Node Burst — tx-impact

- **Файл:** [`fxRenderer.ts:736–782`](simulator-ui/v2/src/render/fxRenderer.ts:736)
- **Описание:** Контурное свечение вокруг ноды при получении транзакции. 4 слоя stroke с убывающим blur.
- **Стоимость:** 🔴🔴 Очень высокая — 4× stroke с `shadowBlur` + `evenodd` clip + `Path2D`
  - Layer 1: `shadowBlur = max(spx(12), nodeR*0.8) * alpha * blurK` ([`:759`](simulator-ui/v2/src/render/fxRenderer.ts:759))
  - Layer 2: `shadowBlur = max(spx(8), nodeR*0.5) * alpha * blurK` ([`:765`](simulator-ui/v2/src/render/fxRenderer.ts:765))
  - Layer 3: `shadowBlur = max(spx(4), nodeR*0.25) * alpha * blurK` ([`:771`](simulator-ui/v2/src/render/fxRenderer.ts:771))
  - Layer 4: white core, `shadowBlur=0` ([`:777`](simulator-ui/v2/src/render/fxRenderer.ts:777))
- **Визуально:** Равномерное контурное свечение, clipped снаружи ноды (interior тёмный), 4-слойное с убывающей шириной

#### 6. Node Burst — glow

- **Файл:** [`fxRenderer.ts:783–810`](simulator-ui/v2/src/render/fxRenderer.ts:783)
- **Описание:** Мягкий расширяющийся круг свечения. `radialGradient` + `shadowBlur`.
- **Стоимость:** 🔴 Высокая — `createRadialGradient` + `shadowBlur = max(spx(18), nodeR*1.4) * a * blurK` ([`:804`](simulator-ui/v2/src/render/fxRenderer.ts:804))
- **Визуально:** Расширяющееся мягкое свечение от центра ноды, `screen` blend

#### 7. Node Burst — clearing

- **Файл:** [`fxRenderer.ts:811–836`](simulator-ui/v2/src/render/fxRenderer.ts:811)
- **Описание:** Bloom + shockwave ring. Default burst стиль для clearing.
- **Стоимость:**
  - 🔴 Bloom: `shadowBlur = spx(30) * alpha * blurK` ([`:821`](simulator-ui/v2/src/render/fxRenderer.ts:821)) + `arc` fill
  - 🟡 Shockwave: `arc` stroke без blur, lineWidth уменьшается ([`:830`](simulator-ui/v2/src/render/fxRenderer.ts:830))
- **Визуально:** Яркий центральный bloom + расширяющееся кольцо

#### 8. Flash Overlay (Screen-space)

- **Файл:** [`useRenderLoop.ts:418–437`](simulator-ui/v2/src/composables/useRenderLoop.ts:418)
- **Описание:** Полноэкранная вспышка при clearing. Radial gradient в screen-space (не двигается с камерой).
- **Стоимость:** 🟡 Средняя — `createRadialGradient` + `fillRect` на весь canvas. Один вызов за кадр. Убывает на 0.03 за кадр.
- **Визуально:** Мягкая цветная вспышка от центра к краям

---

### DOM Layer

#### 1. Node Labels (LabelsOverlayLayers)

- **Описание:** HTML-элементы поверх canvas с именами нод. Позиционируются через CSS transform на основе camera pan/zoom.
- **Стоимость:** DOM layout — O(видимых нод). Может быть дорого при большом количестве нод если не используется виртуализация.
- **Визуально:** Текстовые метки под/над нодами

#### 2. Floating Labels

- **Описание:** Временные метки (например, суммы транзакций), которые появляются и затухают. Управляются через [`pruneFloatingLabels()`](simulator-ui/v2/src/composables/useRenderLoop.ts:449).
- **Стоимость:** DOM — аналогично node labels, но количество ограничено TTL
- **Визуально:** Анимированные текстовые метки

#### 3. Edge Tooltips

- **Описание:** Тултипы при hover над рёбрами. DOM-элементы.
- **Стоимость:** DOM — единичный элемент, минимальная стоимость
- **Визуально:** Всплывающая подсказка с информацией о ребре

---

## Матрица: тип взаимодействия × эффект → поведение

| Элемент | Hover | Click | Drag | Wheel | Physics Running |
|---------|-------|-------|------|-------|-----------------|
| **Bloom underlay** | Полный blur | Полный blur | Пропущен | blurK=0 (пропущен) | Полный blur |
| **Body fill gradient** | Gradient (High) | Gradient (High) | Solid alpha=0.45 | Gradient (High) | Gradient (High) |
| **Neon Rim glow** | shadowBlur ON | shadowBlur ON | Пропущен | shadowBlur=0, stroke only | shadowBlur ON |
| **Rim white core** | Рисуется | Рисуется | Пропущен | Рисуется | Рисуется |
| **Icons** | Рисуются | Рисуются | Рисуются (alpha=0.7) | Рисуются | Рисуются |
| **Badge** | Рисуется | Рисуется | Рисуется | Рисуется | Рисуется |
| **Selection Glow** | — | Появляется (blur/fallback) | Пропущен | Blur=0 → fallback | Blur ON |
| **Active Node Glow** | — | — | Пропущен | Blur=0 → fallback | Blur ON |
| **Links base** | Полный | Полный | LOD focus, boosted alpha | Полный | Полный |
| **Links overlay** | Полный | Полный | Пропущен | Полный | Полный |
| **FX Sparks** | Рисуются | Рисуются | Рисуются (blurK=0) | Рисуются (blurK=0) | Рисуются |
| **FX EdgePulse** | Рисуется | Рисуется | Рисуется (blurK=0) | Рисуется (blurK=0) | Рисуется |
| **FX NodeBurst** | Рисуется | Рисуется | Рисуется (blurK=0) | Рисуется (blurK=0) | Рисуется |
| **Flash overlay** | Рисуется | Рисуется | Рисуется | Рисуется | Рисуется |
| **DOM labels** | Видимы | Видимы | Видимы | Видимы | Видимы |

---

## Таблица стоимости эффектов

| Эффект | Стоимость | Сложность | DPR влияние | Комментарий |
|--------|-----------|-----------|-------------|-------------|
| `shadowBlur` | 🔴 Высокая | GPU gaussian blur per draw call | Квадратичное (blur radius × pixel area) | Главный bottleneck. Масштабируется с `blurK` и радиусом ноды |
| `createLinearGradient` | 🟡 Средняя | CPU: создание объекта + GPU: интерполяция | Линейное | Умеренная стоимость; заметно при ×100 нод |
| `createRadialGradient` | 🟡 Средняя | CPU + GPU интерполяция | Линейное | Используется в FX glow burst и flash overlay |
| `arc` / `rect` path | ⚡ Низкая | CPU path construction | Минимальное | Базовые примитивы, пренебрежимая стоимость |
| `fill()` / `stroke()` без blur | ⚡ Низкая | GPU rasterization | Линейное | Стандартная растеризация |
| `fillRect` / `strokeRect` | ⚡ Низкая | GPU | Линейное | Flash overlay — один вызов на весь canvas |
| `drawImage` (icon sprites) | 🟡 Средняя | GPU texture upload + blit | Зависит от sprite size | Используется в [`drawGlowSprite`](simulator-ui/v2/src/render/glowSprites.ts:126); кэшируется |
| DOM label updates | 🟡 Средняя | CPU layout + paint | — (DOM) | Может вызвать reflow при массовых обновлениях |
| `screen` composite | ⚡ Низкая | GPU blend | Минимальное | Дешевле чем blur, но дороже чем source-over |
| `lighter` composite | ⚡ Низкая | GPU blend | Минимальное | Аддитивное смешивание для FX |
| `evenodd` clip + Path2D | 🟡 Средняя | GPU clip setup | Зависит от сложности path | Используется в tx-impact burst ([`:752`](simulator-ui/v2/src/render/fxRenderer.ts:752)) |
| `withAlpha()` | ⚡ Минимальная | CPU string concat | — | LRU кэш до [`512`](simulator-ui/v2/src/render/color.ts:7) записей |

---

## Классификация элементов по допустимости деградации

### ✅ Никогда не деградировать (стоимость ≈ 0)

| Элемент | Причина |
|---------|---------|
| Icons ([`drawNodeIcon`](simulator-ui/v2/src/render/nodePainter.ts:18)) | Несколько arc/rect, нет blur/gradient |
| Badge pip ([`drawNodeBadge`](simulator-ui/v2/src/render/nodePainter.ts:82)) | Один arc + fill |
| Rim white core ([`:299–311`](simulator-ui/v2/src/render/nodePainter.ts:299)) | Stroke без blur |
| Body solid fill (Med/Low) ([`:248`](simulator-ui/v2/src/render/nodePainter.ts:248)) | Один fill call |
| Links base strokes ([`:76–104`](simulator-ui/v2/src/render/baseGraph.ts:76)) | moveTo+lineTo+stroke |
| DOM labels | Вне canvas pipeline |

### ⚠️ Деградировать только при drag/wheel (дорогие)

| Элемент | Механизм деградации | Источник |
|---------|---------------------|----------|
| Bloom shadowBlur | `blurK=0` → блок пропущен | [`nodePainter.ts:217`](simulator-ui/v2/src/render/nodePainter.ts:217) |
| Rim glow shadowBlur | `blurK=0` → `shadowBlur=0` | [`nodePainter.ts:287`](simulator-ui/v2/src/render/nodePainter.ts:287) |
| Selection Glow shadowBlur | `blurK≤0.1` → fallback stroke | [`baseGraph.ts:175`](simulator-ui/v2/src/render/baseGraph.ts:175) |
| Active Node Glow shadowBlur | `blurK≤0.1` → fallback stroke | [`baseGraph.ts:237`](simulator-ui/v2/src/render/baseGraph.ts:237) |
| FX spark/pulse shadowBlur | `blurK=0` → `shadowBlur=0` | Все FX blur умножены на `blurK` |
| FX gradients | `allowGradients=false` при `intensity≥0.5` | [`fxRenderer.ts:371`](simulator-ui/v2/src/render/fxRenderer.ts:371) |

### 🔄 Деградировать полностью при тяжёлых сценах (средние)

| Элемент | Механизм деградации |
|---------|---------------------|
| Body fill gradient (High) | Adaptive quality → Med/Low → solid fill |
| FX radial gradient (glow burst) | `blurK=0` → всё ещё radialGradient, но без blur |
| FX Spark beam gradient | `allowGradients=false` → solid color |
| GlowSprite drawImage | Адаптивная quality → уменьшенный `k` коэффициент ([`:202`](simulator-ui/v2/src/render/nodePainter.ts:202)) |
| DPR resolution | Adaptive DPR clamp: `2.0→1.25→1.0` при низком FPS |

---

## Вспомогательные модули

### Glow Sprites ([`glowSprites.ts`](simulator-ui/v2/src/render/glowSprites.ts))

Система кэшированных off-screen canvas для замены per-frame `shadowBlur` в software mode:

- Кэш: до [`MAX_CACHE=260`](simulator-ui/v2/src/render/glowSprites.ts:20) записей с LRU-эвикцией
- Ключ кэша: kind + shape + color + размеры (квантованные с шагом 0.5) ([`:27–39`](simulator-ui/v2/src/render/glowSprites.ts:27))
- [`getGlowSprite()`](simulator-ui/v2/src/render/glowSprites.ts:48) — создаёт off-screen canvas с blur один раз
- [`drawGlowSprite()`](simulator-ui/v2/src/render/glowSprites.ts:126) — `drawImage` с composite (обычно `screen`)
- Поддерживает `bloom` (fill with shadow) и `rim` (stroke with shadow) ([`:83–113`](simulator-ui/v2/src/render/glowSprites.ts:83))

### Link Geometry ([`linkGeometry.ts`](simulator-ui/v2/src/render/linkGeometry.ts))

- [`getLinkTermination()`](simulator-ui/v2/src/render/linkGeometry.ts:3) — вычисляет точку пересечения ребра с контуром ноды
- Circle: пересечение с окружностью ([`:19–21`](simulator-ui/v2/src/render/linkGeometry.ts:19))
- Rounded-rect: ray-box intersection ([`:25–33`](simulator-ui/v2/src/render/linkGeometry.ts:25))
- Стоимость: ⚡ Минимальная — чистая математика, без canvas calls

### Color Utilities ([`color.ts`](simulator-ui/v2/src/render/color.ts))

- [`withAlpha()`](simulator-ui/v2/src/render/color.ts:37) — конвертирует `#hex` → `rgba(r,g,b,a)`
- LRU кэш до [`512`](simulator-ui/v2/src/render/color.ts:7) записей hex→RGB парсинга
- Обрабатывает short (`#rgb`) и long (`#rrggbb`) hex
- Passthrough для `rgba(...)` и `hsla(...)` ([`:40`](simulator-ui/v2/src/render/color.ts:40))

---

## Решённые проблемы и их причины (для справки)

### Мерцание сцены при hover мыши

- **Причина:** `mark()` вызывался на каждый `pointermove` включая hover (buttons=0), что приводило к постоянному cycling intensity 0→1→0 и видимому мерцанию blur-эффектов
- **Решение:** `mark()` только при `ev.buttons !== 0` ([`useAppCanvasInteractionsWiring.ts:59`](simulator-ui/v2/src/composables/useAppCanvasInteractionsWiring.ts:59))
- **Комментарий в коде:** «Hover (buttons === 0) must NOT trigger quality reduction — root cause of the bug»

### Задержка 300-600ms при click/wheel из idle

- **Причина:** Первый кадр после deep idle рендерился в High quality с warmup period (`warmupMs=2000`), что приводило к тяжёлому первому кадру без возможности adaptive downgrade
- **Решение:**
  - `wakeUp('user')` на все user events ([`useAppCanvasInteractionsWiring.ts`](simulator-ui/v2/src/composables/useAppCanvasInteractionsWiring.ts))
  - `lastWakeSource='user'` пропускает warmup: [`inWarmup = lastWakeSource === 'user' ? false : ...`](simulator-ui/v2/src/composables/useRenderLoop.ts:590)
  - `mark({instant:true})` на wheel для мгновенного `intensity=1` → `blurK=0`

### Мигание Selection Glow при клике

- **Причина:** `mark({instant:true})` вызывался на `click` → `intensity` мгновенно 1→0, что давало один кадр без blur (glow пропадал) а потом возвращался — видимый blink
- **Решение:** Убрать `mark()` из click handler — click это мгновенное действие, не continuous interaction ([`useAppCanvasInteractionsWiring.ts:42`](simulator-ui/v2/src/composables/useAppCanvasInteractionsWiring.ts:42))

### Пропадание иконок при drag

- **Причина:** `dragMode` early return в [`drawNodeShape()`](simulator-ui/v2/src/render/nodePainter.ts:164) изначально не включал отрисовку иконок и бейджов
- **Решение:** [`drawNodeIcon()`](simulator-ui/v2/src/render/nodePainter.ts:186) и [`drawNodeBadge()`](simulator-ui/v2/src/render/nodePainter.ts:187) вынесены из основного пути и вызываются из drag fast-path с уменьшенным alpha (0.7)

---

## Рекомендации для будущей работы

### Кэширование градиентов

Body fill gradient ([`nodePainter.ts:243`](simulator-ui/v2/src/render/nodePainter.ts:243)) создаёт `createLinearGradient` каждый кадр для каждой ноды в High quality. Возможности:
- Кэшировать `CanvasGradient` по ключу `(x, y, w, h, color)` — экономия CPU на 100+ нод
- Или использовать precomputed gradient texture аналогично glowSprites

### Passive wheel listener

`onCanvasWheel` в [`useAppCanvasInteractionsWiring.ts:68`](simulator-ui/v2/src/composables/useAppCanvasInteractionsWiring.ts:68) — если wheel не вызывает `preventDefault()`, можно сделать listener passive для лучшего scroll performance.

### GlowSprite оптимизации

- [`MAX_CACHE=260`](simulator-ui/v2/src/render/glowSprites.ts:20) может быть недостаточно для сцен с 100+ нод разных размеров
- Квантизация с шагом 0.5 ([`q()`](simulator-ui/v2/src/render/glowSprites.ts:22)) хорошо снижает уникальность, но при continuous zoom может генерировать много вариантов
- Рассмотреть atlas-подход: один большой canvas с несколькими sprite'ами

### Возможности WebGL для blur

`shadowBlur` — самая дорогая операция в рендере. Переход на WebGL/WebGPU для blur-эффектов может дать:
- GPU-native gaussian blur через shader passes
- Instanced rendering для нод (один draw call для всех)
- Но требует полной переработки render pipeline

### FX Budget на тяжёлых сценах

Текущие лимиты частиц ([`useRenderLoop.ts:457`](simulator-ui/v2/src/composables/useRenderLoop.ts:457)):
- Low: 120, Med: 180, High: 220
- Масштабируются `fxBudgetScale` (smooth EMA)
- Рассмотреть приоритизацию частиц (newer > older) вместо простого drop oldest
