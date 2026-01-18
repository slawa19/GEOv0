# Техническое задание (ТЗ)
# GEO Simulator — Этап 1: Интерактивная визуализация графа (Game UI)

**Версия:** 1.0 (Draft)
**Размещение:** `docs/ru/simulator/frontend/` (рядом с фазовыми спеками)

## 0. Источники и контекст

ТЗ синтезировано на основе следующих спецификаций:

- [docs/ru/simulator/frontend/archive/SPEC-GEO-Simulator-Phase1.md](docs/ru/simulator/frontend/archive/SPEC-GEO-Simulator-Phase1.md:1) (архив)
- [docs/ru/simulator/frontend/archive/MAIN Spec GEO Simulator Implementation (Vue 3 Stack).md](docs/ru/simulator/frontend/archive/MAIN%20Spec%20GEO%20Simulator%20Implementation%20(Vue%203%20Stack).md:1) (архив)
- [docs/ru/simulator/frontend/Игровой интерфейс симулятора GEO.md](docs/ru/simulator/frontend/%D0%98%D0%B3%D1%80%D0%BE%D0%B2%D0%BE%D0%B9%20%D0%B8%D0%BD%D1%82%D0%B5%D1%80%D1%84%D0%B5%D0%B9%D1%81%20%D1%81%D0%B8%D0%BC%D1%83%D0%BB%D1%8F%D1%82%D0%BE%D1%80%D0%B0%20GEO.md:1)
- [docs/ru/simulator/specs/GENERAL-simulator-application.md](docs/ru/simulator/specs/GENERAL-simulator-application.md:1)

Цель Этапа 1 — построить интерактивный, «игровой» прототип визуализации сети доверия GEO, демонстрирующий:

- участников как узлы;
- линии доверия как ребра;
- процесс транзакции как поток по связи;
- процесс клиринга как схлопывание долгов (визуальная симуляция).

Визуальный стиль должен **кардинально отличаться** от Admin UI, но оставаться **читаемым и консервативным** (это финансовые операции, а не катастрофа): аккуратный glow/bloom, искры/частицы, плавная физика и минималистичный HUD.

---

## 0.5. Промпты для генерации скриншотов (AI image prompts)

Ниже — набор промптов для генерации скриншотов (например, Nano Banana и аналогичные инструменты). Цель — быстро получить согласуемые изображения UI/VFX, по которым затем корректируем спецификацию и/или функционал.

Контекст проекта GEO (важно для корректных скриншотов):

- **Линии доверия** образуют кредитную сеть, где движение обязательств может идти **по маршруту из нескольких ребер** (multi-hop), а не только по одной прямой связи.
- **Транзакция (payment)** на уровне протокола может проходить по маршруту A → B → C → ... → Z (в Этапе 1 допускается упрощение, но UI-метафора не должна «запирать» нас на A→B).
- **Клиринг (netting)** по смыслу работает с **циклами долгов** и может затрагивать **3 узла и более** (длина цикла не фиксирована). В Этапе 1 можно начинать с треугольников, но визуальный язык должен масштабироваться на 4–6+ узлов.

### 0.5.0. Общие требования ко всем скриншотам

- формат: 16:9, 1920×1080 (или 2560×1440)
- стиль: игровая тактическая карта, deep space фон, читаемый и консервативный финансовый интерфейс (без «катастрофы»)
- граф: 35–50 узлов, 2–4 связи на узел
- типы узлов:
  - `business`: квадрат, базовый цвет emerald
  - `person`: круг, базовый цвет blue
- линии доверия: тонкие, полупрозрачные (idle), **без чисел** на ребрах
- при выборе узла: подсветка только инцидентных связей, остальные связи затемнены
- индикация баланса (только на выбранном/фокусном узле): дополнительный glow
  - credit (balance > 0): cyan glow
  - debt (balance < 0): orange glow
  - около 0: нейтральный бело-slate glow
- HUD:
  - карточка участника рядом с выбранным узлом: Тип (иконка) + Имя + Баланс (signed)
  - панель управления внизу: две кнопки `Single Tx`, `Run Clearing`

Полезные элементы интерфейса (желательно показать хотя бы на 1–2 скриншотах):

- компактная верхняя строка статуса (как «HUD» в игре): `Nodes`, `Links`, `Active Tx`, `Clearing` (idle/running)
- небольшая легенда цветов (credit cyan / debt orange) в углу, чтобы зритель сразу считал семантику glow

Если инструмент поддерживает уточнения, добавлять:

- clean UI
- no minimap
- no camera controls UI
- no admin dashboard aesthetics
- no extra labels on edges

### 0.5.1. Скриншот A — общий вид сети (idle)

```text
Game UI screenshot, deep space tactical map background, dark blue-black with subtle starfield and faint nebula haze.

Show an interactive trust network graph centered on screen:
- 40 nodes total, mixed types: 20% business nodes as small emerald squares, 80% person nodes as small blue circles.
- Each node has soft bloom, readable and conservative.
- Trustlines are thin slate lines with very low opacity, no numbers on lines.

HUD overlay:
- Bottom center minimal HUD panel with two buttons: Single Tx and Run Clearing.
- No active effects, no sparks.

Overall look: clean, game-like, financial network visualization, high readability.
```

### 0.5.2. Скриншот B — выбранный узел + карточка участника

```text
Game UI screenshot of a deep space tactical network map.

Select one node near the center-right.
- The selected node has a stronger glow that indicates balance sign:
  - positive balance: cyan glow
  - negative balance: orange glow
- Keep node base color and shape by type (business emerald square, person blue circle).

Highlight only the selected node incident trustlines (brighter), dim all other trustlines to near invisible.

Show a glassmorphism HUD card near the selected node (not blocking the graph):
- Type icon (business or person)
- Name: Aurora Market
- Balance: +1250 GC

Bottom HUD controls panel with Single Tx and Run Clearing.
```

### 0.5.3. Скриншот C — транзакция (Tx) по линии доверия

```text
Game UI screenshot of a trust network during a transaction.

Show a transaction from node A to node B along a single trustline.
- The trustline becomes bright and shimmering while the transaction is active.
- A single spark (white core with cyan trail) moves slowly along the trustline from A to B.
- When the spark reaches node B, node B flashes briefly (not an explosion).
- A floating amount label appears near node B and moves upward while fading out: 125 GC.

Keep the rest of the network visible but slightly subdued.
HUD:
- Bottom panel with Single Tx and Run Clearing.
```

### 0.5.4. Скриншот D — транзакция (Tx) по маршруту из нескольких узлов (multi-hop)

Этот экран нужен, чтобы заранее согласовать визуальную метафору маршрутизации по нескольким линиям доверия.

```text
Game UI screenshot of a trust network during a multi-hop transaction.

Highlight a route of 4 nodes (A -> B -> C -> D).
- The route consists of 3 trustlines.
- A single transaction spark moves along the route step-by-step:
  - first along A->B, then B->C, then C->D
- The currently active trustline is bright and shimmering; the remaining route trustlines are moderately highlighted.
- When the spark reaches the final node D, node D flashes briefly.
- A floating amount label appears near node D and moves upward while fading out: 125 GC.

HUD:
- Bottom panel with Single Tx and Run Clearing.
- Optional top status line: Nodes 40, Links 90, Active Tx 1.
No edge numbers.
```

### 0.5.5. Скриншот E — клиринг (Clearing) по линии доверия (локальный случай)

```text
Game UI screenshot of a trust network during clearing (netting).

Show a clearing event on one trustline:
- Two sparks move toward each other along the same trustline:
  - cyan spark represents credit
  - orange spark represents debt
- The trustline becomes bright and shimmering while sparks move.
- Sparks meet at the midpoint: a compact flash and a small burst of tiny sparks (particle dust), no violent explosion.
- A floating amount label appears near the midpoint and moves upward while fading out: 300 GC.
- After the event, the trustline returns to normal faint idle state.

HUD:
- Bottom panel with Single Tx and Run Clearing (Run Clearing disabled).
```

### 0.5.6. Скриншот F — клиринг по циклу из 4–6 узлов (cycle clearing)

Этот экран нужен, чтобы визуально зафиксировать, что клиринг в GEO может охватывать несколько узлов и связей, а не один сегмент.

```text
Game UI screenshot of a trust network during cycle clearing (netting across multiple nodes).

Highlight a cycle of 5 nodes (A-B-C-D-E-A) in the graph.
- All cycle trustlines are highlighted; non-cycle trustlines are dimmed.

Clearing visualization:
- On each cycle trustline, two sparks move toward each other:
  - cyan spark represents credit
  - orange spark represents debt
- Each active trustline becomes bright and shimmering while sparks move.
- Sparks meet near the midpoint of each trustline with compact flashes and tiny particle dust.

Add one floating amount label near the cycle center that moves upward while fading out: 300 GC.
After the event, all cycle trustlines return to normal faint idle state.

HUD:
- Bottom panel with Single Tx and Run Clearing (Run Clearing disabled).
- Optional top status line: Clearing running.
Conservative, readable, not explosive.
```


## 1. Термины и маппинг на GEO

### 1.1. Участник (Participant)
Сущность сети (человек/бизнес). В симуляторе отображается как узел (node).

### 1.2. Линия доверия (Trustline)
Кредитная связь между двумя участниками. В симуляторе отображается как ребро (link).

Минимальная интерпретация для Этапа 1:

- связь существует между A и B;
- у связи есть «сила/ёмкость» (trustLimit) и/или «важность» (strength);
- визуально связь может быть «пассивной» или «активной».

### 1.3. Транзакция (Transaction)
Процесс передачи обязательства/стоимости между участниками.

Контекст GEO: транзакция может проходить **по маршруту из нескольких линий доверия** (multi-hop). В Этапе 1 транзакция **не обязана** строго соответствовать реальной маршрутизации протокола; важна наглядная визуализация «потока» по сети.

### 1.4. Клиринг (Clearing)
Схлопывание взаимных долгов (netting) по циклам/цепочкам.

Контекст GEO: клиринг может происходить по **циклам длиной 3 узла и более** (длина цикла не фиксирована).

В Этапе 1 допускается упрощенная эвристика (например, поиск треугольников) с обязательным визуальным эффектом схлопывания и последующим изменением отображаемых балансов (демо), но UI/визуализация должны поддерживать идею «цикл может быть длиннее».

---

## 2. Цели, рамки и критерии успеха

### 2.1. Цель Этапа 1
Сделать MVP интерактивного графа на Vue 3, демонстрирующего «живую систему» (physics + VFX) и базовые процессы (Tx + Clearing), согласно [docs/ru/simulator/frontend/archive/SPEC-GEO-Simulator-Phase1.md](docs/ru/simulator/frontend/archive/SPEC-GEO-Simulator-Phase1.md:14).


### 2.2. В рамке (In scope)

1) Отрисовка графа (узлы + ребра) на Canvas 2D.

2) Базовая физика раскладки (force-directed), стабилизация, анимация idle «дыхания».

3) Интерактивность:

- клик по узлу открывает HUD-карточку участника;
- подсветка связей выбранного узла + затемнение остальных;
- drag узлов (перетаскивание) с последующей «дораскладкой» физикой (узел не pinned).

4) Анимации процессов:

- транзакция: искра (световой импульс) медленно идет по линии доверия от A к B, при достижении — вспышка узла получателя и «улетающий» вверх лейбл суммы;
- клиринг: две искры разных цветов (debt/credit) идут навстречу по линии доверия и встречаются в центре, после чего происходит вспышка и рассыпание искрами, а лейбл суммы долга улетает вверх.

5) Источник данных:

- загрузка из API, если доступно;
- fallback на генерацию моков.

### 2.3. Вне рамки (Out of scope)

- полноценная экономическая симуляция;
- точное соответствие алгоритмам роутинга/клиринга ядра;
- pan/zoom камера;
- история событий, миникарта, режимы отображения (кроме минимальных переключателей качества/отладки).

### 2.4. Критерии успеха

- 60 FPS при 100+ узлах на типовом ноутбуке (целевой критерий из [docs/ru/simulator/frontend/archive/SPEC-GEO-Simulator-Phase1.md](docs/ru/simulator/frontend/archive/SPEC-GEO-Simulator-Phase1.md:17)).
- «Игровой режим»: deep space фон, glow/bloom на узлах, аддитивные эффекты и частицы.
- Стабильная интерактивность (выбор узла, подсветка связей, drag).
- Демонстрация Tx/Clearing через явные кнопки управления.

---

## 3. Технологический стек (Stage 1)

### 3.1. Обязательный стек

- Vue 3 (Composition API)
- TypeScript
- Pinia
- Canvas 2D рендеринг
- d3-force для физики (только математика) согласно [docs/ru/simulator/frontend/archive/SPEC-GEO-Simulator-Phase1.md](docs/ru/simulator/frontend/archive/SPEC-GEO-Simulator-Phase1.md:32)
- Tailwind CSS для HUD
- lucide-vue-next для иконок

Основной паттерн исполнения: единый render loop через [`javascript.requestAnimationFrame()`](docs/ru/simulator/frontend/archive/MAIN%20Spec%20GEO%20Simulator%20Implementation%20(Vue%203%20Stack).md:86) после [`vue.onMounted()`](docs/ru/simulator/frontend/archive/MAIN%20Spec%20GEO%20Simulator%20Implementation%20(Vue%203%20Stack).md:99).

### 3.2. Опционально (не блокирует Этап 1)

- `@tsparticles/vue3` + `tsparticles-slim` для фоновой звездной пыли (если проще, чем кастомный starfield), ориентир [docs/ru/simulator/frontend/Игровой интерфейс симулятора GEO.md](docs/ru/simulator/frontend/%D0%98%D0%B3%D1%80%D0%BE%D0%B2%D0%BE%D0%B9%20%D0%B8%D0%BD%D1%82%D0%B5%D1%80%D1%84%D0%B5%D0%B9%D1%81%20%D1%81%D0%B8%D0%BC%D1%83%D0%BB%D1%8F%D1%82%D0%BE%D1%80%D0%B0%20GEO.md:356)
- howler для звука (можно отложить)

---

## 4. Архитектурный обзор (Stage 1)

### 4.1. Общая схема слоёв

1) **Canvas (рендер ядра)**

- фон (deep space + bokeh/stars)
- ребра (passive)
- ребра (active)
- узлы (bloom + body)
- частицы (Tx/Clearing)
- линия-поводок к карточке (опционально)

Порядок слоёв фиксируется как в [docs/ru/simulator/frontend/archive/MAIN Spec GEO Simulator Implementation (Vue 3 Stack).md](docs/ru/simulator/frontend/archive/MAIN%20Spec%20GEO%20Simulator%20Implementation%20(Vue%203%20Stack).md:23) и [docs/ru/simulator/frontend/archive/SPEC-GEO-Simulator-Phase1.md](docs/ru/simulator/frontend/archive/SPEC-GEO-Simulator-Phase1.md:90).

2) **HTML HUD (оверлей)**

- карточка участника
- панель управления (Tx/Clearing)
- (опционально) debug overlay (FPS, количество частиц)

### 4.2. Потоки данных

Mermaid (высокоуровневая архитектура):

```mermaid
flowchart TD
  UI[HUD компоненты] --> Store[Pinia store]
  Store --> Scene[GeoSimulatorMesh]
  Scene --> Data[useGeoData]
  Scene --> Physics[useGeoPhysics]
  Scene --> Render[useGeoRenderer]
  Scene --> Input[useInteraction]
  Physics --> Render
  Input --> Store
  Store --> Render
```

Ключевой принцип: Vue/Pinia управляют **состоянием и событиями**, а Canvas-рендер и физика работают в императивном цикле (минимум реактивных перерисовок DOM).

### 4.3. Границы ответственности

- Pinia: «что выбрано», «какие эффекты запущены», «какой режим качества».
- Physics: координаты/скорости узлов, интеграция force simulation, drag.
- Renderer: рисование кадра из текущего состояния.
- Interaction: pointer события, hit-test, drag state.
- VFX: искры/частицы, вспышки, кольцевой импульс (мягкий, без «взрывной» метафоры).

---

## 5. Контракт данных (Stage 1)

### 5.1. Минимальная модель данных

#### [src/types/simulator/geo-graph.ts](src/types/simulator/geo-graph.ts:1)

- `GeoNode`
  - `id: string`
  - `type: 'business' | 'person'`
  - `name: string`
  - `balance: number` (для HUD; в Этапе 1 допускается демо-обновление)
  - `trustLimitTotal?: number` (опционально, если API не дает)
  - `x?: number, y?: number` (мировые координаты; если нет — инициализируются генератором)
  - `vx?: number, vy?: number` (для симуляции)
  - `fx?: number, fy?: number` (только на время drag)
  - `renderX: number, renderY: number` (derived для idle «дыхания»)

- `GeoLink`
  - `id: string`
  - `source: string` (id)
  - `target: string` (id)
  - `trustLimit?: number`
  - `strength?: number` (для d3-force link strength)

- `GeoGraphSnapshot`
  - `nodes: GeoNode[]`
  - `links: GeoLink[]`
  - `meta?: { seed?: string; generatedAt?: string; }`

Ориентир на формат из [docs/ru/simulator/frontend/archive/MAIN Spec GEO Simulator Implementation (Vue 3 Stack).md](docs/ru/simulator/frontend/archive/MAIN%20Spec%20GEO%20Simulator%20Implementation%20(Vue%203%20Stack).md:130) и приложения с примером JSON в [docs/ru/simulator/frontend/Игровой интерфейс симулятора GEO.md](docs/ru/simulator/frontend/%D0%98%D0%B3%D1%80%D0%BE%D0%B2%D0%BE%D0%B9%20%D0%B8%D0%BD%D1%82%D0%B5%D1%80%D1%84%D0%B5%D0%B9%D1%81%20%D1%81%D0%B8%D0%BC%D1%83%D0%BB%D1%8F%D1%82%D0%BE%D1%80%D0%B0%20GEO.md:572).

### 5.2. Требования к API (если доступно)

В Этапе 1 фронтенд должен пытаться загрузить snapshot графа. Бэкендовый контракт:

- `GET /graph/snapshot` возвращает `GeoGraphSnapshot`.

Если API в текущем репозитории не предоставляет этот эндпоинт, фронтенд использует генератор моков (см. [src/composables/simulator/useGeoData.ts](src/composables/simulator/useGeoData.ts:1)).

### 5.3. Моки и генерация

Требования к генерации:

- стартовая раскладка: «golden angle / спираль» как в [docs/ru/simulator/frontend/archive/SPEC-GEO-Simulator-Phase1.md](docs/ru/simulator/frontend/archive/SPEC-GEO-Simulator-Phase1.md:104)
- количество узлов: 35–50 для базового демо, поддержка 100+ для стресс-теста
- топология: 2–4 связи на узел, с недопущением дублей (идея из [docs/ru/simulator/frontend/archive/MAIN Spec GEO Simulator Implementation (Vue 3 Stack).md](docs/ru/simulator/frontend/archive/MAIN%20Spec%20GEO%20Simulator%20Implementation%20(Vue%203%20Stack).md:217))

---

## 6. State management (Pinia)

### 6.1. Store

#### [src/stores/simulator/geoSimulator.store.ts](src/stores/simulator/geoSimulator.store.ts:1)

Состояние:

- `graph: { nodes: GeoNode[]; links: GeoLink[] }`
- `status: 'idle' | 'loading' | 'ready' | 'error'`
- `selectedNodeId: string | null`
- `highlightMode: 'neighbors'` (в Этапе 1 фиксировано)
- `effects:`
  - `tx: { running: boolean; queue: TxEvent[] }`
  - `clearing: { running: boolean; queue: ClearingEvent[] }`
- `quality:`
  - `targetFps: 60`
  - `maxParticles: number`
  - `bloomQuality: 'low' | 'high'`
  - `useDpr: boolean`
- `debug:`
  - `showFps: boolean`
  - `showHitAreas: boolean`

Действия:

- `loadGraph()` — API → fallback на моки
- `selectNode(nodeId | null)`
- `triggerSingleTx()` — инициирует Tx событие (выбор пары узлов по эвристике)
- `triggerClearing()` — инициирует clearing событие (поиск цикла или демо-эвристика)
- `setQuality(patch)`

Геттеры:

- `selectedNode` (по `selectedNodeId`)
- `adjacency` (карта соседей для подсветки)

Принцип: store хранит **детерминированное состояние**, а детали кадровой симуляции (массив частиц, dt, fps) живут в composables/движке рендера.

---

## 7. Vue компоненты (Stage 1)

Ниже — целевая декомпозиция. Пути даны как контракты для реализации.

### 7.1. Контейнер/сцена

#### [src/components/simulator/GeoSimulatorMesh.vue](src/components/simulator/GeoSimulatorMesh.vue:1)

Роль: оркестратор.

Ответственность:

- создает `<canvas>` и подгоняет размер (включая DPR);
- запускает цикл [`javascript.requestAnimationFrame()`](docs/ru/simulator/frontend/archive/MAIN%20Spec%20GEO%20Simulator%20Implementation%20(Vue%203%20Stack).md:86);
- связывает: store → physics → renderer;
- подключает pointer события и делегирует в interaction.

События:

- `pointerdown/move/up` на canvas
- `click` на canvas (если не было drag)

### 7.2. HUD карточка участника

#### [src/components/simulator/GeoNodeCard.vue](src/components/simulator/GeoNodeCard.vue:1)

Роль: отображение участника.

Требования:

- glassmorphism (backdrop blur, градиенты), ориентир на стиль карточки в [docs/ru/simulator/frontend/archive/MAIN Spec GEO Simulator Implementation (Vue 3 Stack).md](docs/ru/simulator/frontend/archive/MAIN%20Spec%20GEO%20Simulator%20Implementation%20(Vue%203%20Stack).md:121)
- позиционирование рядом с узлом (по screen coords)
- не блокировать pointer события canvas (по умолчанию `pointer-events: none`, кроме кнопки закрытия)

Props:

- `node: GeoNode`
- `position: { x: number; y: number }`

Emits:

- `close`

### 7.3. Панель управления

#### [src/components/simulator/SimulatorControls.vue](src/components/simulator/SimulatorControls.vue:1)

Роль: кнопки запуска Tx/Clearing.

Требования:

- кнопки: `Single Tx`, `Run Clearing`
- состояния: disabled на время clearing
- визуальный «game HUD»

---

## 8. Composables/модули движка (Stage 1)

### 8.1. Данные

#### [src/composables/simulator/useGeoData.ts](src/composables/simulator/useGeoData.ts:1)

Задачи:

- `loadGraphSnapshot(): Promise<GeoGraphSnapshot>`
- `generateMockGraph(options): GeoGraphSnapshot`
- нормализация/валидация входных данных (минимально)

### 8.2. Физика

#### [src/composables/simulator/useGeoPhysics.ts](src/composables/simulator/useGeoPhysics.ts:1)

Задачи:

- инициализировать d3-force simulation
- поддержать «статичную камеру» (просто мировые координаты на канвасе)
- drag:
  - при `pointerdown` по узлу: выставить `node.fx/node.fy`
  - при `pointermove`: обновлять `fx/fy` в координатах канваса
  - при `pointerup`: очистить `fx/fy`, выставить `simulation.alphaTarget(0)`
- стабилизация:
  - силы: `forceManyBody`, `forceLink`, `forceCenter`/`forceRadial` (по необходимости), `forceCollide`
  - ограничение выхода за bounds (soft constraints)

API:

- `init(nodes, links, options)`
- `tick(dt)`
- `setDrag(nodeId, x, y)`
- `endDrag(nodeId)`

### 8.3. Рендер

#### [src/composables/simulator/useGeoRenderer.ts](src/composables/simulator/useGeoRenderer.ts:1)

Задачи:

- единая функция `render(ctx, state)`
- порядок слоёв (см. раздел 4.1)
- draw calls должны быть чистыми и быстрыми (минимум аллокаций на кадр)

Техники:

- `ctx.globalCompositeOperation = 'lighter'` для аддитивных эффектов (как в [docs/ru/simulator/frontend/archive/MAIN Spec GEO Simulator Implementation (Vue 3 Stack).md](docs/ru/simulator/frontend/archive/MAIN%20Spec%20GEO%20Simulator%20Implementation%20(Vue%203%20Stack).md:28))
- bloom через radial gradients
- отрисовка линий:
  - passive: низкая альфа
  - active: градиент + glow

### 8.4. Интеракции

#### [src/composables/simulator/useInteraction.ts](src/composables/simulator/useInteraction.ts:1)

Задачи:

- hit-test узлов (круг/квадрат) по `renderX/renderY`
- различать `click` и `drag` (порог пикселей)
- маршрутизировать события в store:
  - `selectNode`
  - начало/конец drag

### 8.5. VFX

#### [src/composables/simulator/useVfx.ts](src/composables/simulator/useVfx.ts:1)

Сущности:

- `ParticleTx` (искра транзакции)
- `ParticleDust` (мелкие искры/шлейф)
- `Shockwave` (кольцевой импульс / pulse ring, опционально)

Поведение и ориентиры:

- транзакция: **искра** медленно проходит по линии доверия от отправителя к получателю; при достижении получателя — короткая вспышка узла и появление лейбла суммы (например `125 GC`), который улетает вверх и растворяется.
- клиринг: **две искры** (debt/credit) идут навстречу друг другу по активной линии; в точке встречи — вспышка + рассыпание мелкими искрами; лейбл суммы долга улетает вверх и растворяется. Кольцевой импульс допускается как мягкий визуальный акцент (без ощущения «взрыва»).

---

## 9. UI/UX и визуальные требования (Game Mode)

### 9.0. Минимально необходимый набор элементов интерфейса (MVP)

Фокус Этапа 1 — показать **сеть доверия** и **две финансовые операции** (Tx и Clearing) максимально наглядно и без визуального шума.

Минимальный состав:

1) **Граф (Canvas)**

- узлы-участники:
  - различение типа `business/person` (форма + базовый цвет);
  - подписи имени на холсте по умолчанию **не рисуем** (имя показываем в карточке при выборе), чтобы не перегружать сцену.
- линии доверия (ребра):
  - числовые значения на ребре **не показываем**;
  - `trustLimit/strength` могут влиять на визуал (толщина/альфа) для ощущения «важности» связи.

2) **Интерактивность (Canvas + HUD)**

- клик по узлу → выбор узла и открытие карточки участника;
- подсветка связей выбранного узла + затемнение остальных;
- drag узлов.

3) **HUD (HTML overlay)**

- карточка участника: **Тип + Имя + Баланс** (signed);
- панель управления: `Single Tx` и `Run Clearing`.

4) **VFX (Canvas)**

- Tx как искра по линии + вспышка узла-получателя + «улетающий» лейбл суммы `N GC`;
- Clearing как встречные искры + вспышка в центре + рассыпание искрами + «улетающий» лейбл суммы долга.

### 9.1. Визуальная метафора
Стилистика: космическая тактическая карта (референсы и палитра описаны в [docs/ru/simulator/frontend/Игровой интерфейс симулятора GEO.md](docs/ru/simulator/frontend/%D0%98%D0%B3%D1%80%D0%BE%D0%B2%D0%BE%D0%B9%20%D0%B8%D0%BD%D1%82%D0%B5%D1%80%D1%84%D0%B5%D0%B9%D1%81%20%D1%81%D0%B8%D0%BC%D1%83%D0%BB%D1%8F%D1%82%D0%BE%D1%80%D0%B0%20GEO.md:83)).

### 9.2. Design tokens (минимальный набор)

Берем основу из [docs/ru/simulator/frontend/archive/SPEC-GEO-Simulator-Phase1.md](docs/ru/simulator/frontend/archive/SPEC-GEO-Simulator-Phase1.md:79) и [docs/ru/simulator/frontend/archive/MAIN Spec GEO Simulator Implementation (Vue 3 Stack).md](docs/ru/simulator/frontend/archive/MAIN%20Spec%20GEO%20Simulator%20Implementation%20(Vue%203%20Stack).md:13):

- `bg`: `#020408`
- `nodeBusiness`: `#10b981`
- `nodePerson`: `#3b82f6`
- `linkIdle`: `rgba(148, 163, 184, 0.1)`
- `linkActive`: градиент по направлению искры (цвет искры -> белый -> цвет искры) + мягкий glow
- `sparkTxCore`: `#ffffff`
- `sparkTxTrail`: `#22d3ee`
- `sparkCredit`: `#22d3ee` (cyan)
- `sparkDebt`: `#fb923c` (orange)
- `balanceGlowCredit`: `rgba(34, 211, 238, 0.55)`
- `balanceGlowDebt`: `rgba(251, 146, 60, 0.55)`
- `balanceGlowNeutral`: `rgba(248, 250, 252, 0.25)`
- `amountLabel`: `rgba(248, 250, 252, 0.9)`

### 9.3. Правила подсветки

При выборе узла:

- подсвечиваются только связи, инцидентные выбранному узлу;
- все прочие связи затемняются до opacity около `0.05` (ориентир [docs/ru/simulator/frontend/archive/MAIN Spec GEO Simulator Implementation (Vue 3 Stack).md](docs/ru/simulator/frontend/archive/MAIN%20Spec%20GEO%20Simulator%20Implementation%20(Vue%203%20Stack).md:40));
- невыбранные узлы могут слегка снижать bloom/яркость (опционально).

Дополнительная индикация «кредитор/дебитор» (по балансу):

- при выборе/фокусе узла поверх базового bloom появляется **дополнительный glow**, показывающий знак баланса:
  - `balance > 0` → **credit** (cyan);
  - `balance < 0` → **debt** (orange);
  - `balance ≈ 0` → нейтральный (slate/white).
- интенсивность glow может зависеть от `abs(balance)` (мягкая нелинейная шкала), чтобы «сильные» балансы читались быстрее.

### 9.4. HUD

HUD должен выглядеть «как в игре», но быть функциональным:

- карточка участника: **тип (иконка business/person), имя, баланс (signed)**
- панель управления: две кнопки (`Single Tx`, `Run Clearing`), состояние `disabled` на время clearing

Числа и подписи:

- числовые значения `trustLimit/strength` на ребрах **не выводим**;
- сумма операции визуализируется только как лейбл в эффекте: `N GC` (округление до 0 знаков), который улетает вверх и растворяется.

---

## 10. Анимации и поведение эффектов

### 10.1. Idle (дыхание)

Требование: узлы «живые», но без хаоса.

- для каждого узла вычислять небольшой синусоидальный оффсет по времени (как в [docs/ru/simulator/frontend/archive/SPEC-GEO-Simulator-Phase1.md](docs/ru/simulator/frontend/archive/SPEC-GEO-Simulator-Phase1.md:118))
- амплитуда 1–3 px
- частота 0.8–1.5

### 10.2. Транзакция (Tx)

Требование: показать финансовую транзакцию как понятный, «консервативный» сигнал по линии доверия.

- выбор узлов:
  - если выбран узел: брать соседнюю связь (link) от него;
  - иначе: случайная связь.
- визуал (Canvas):
  - по выбранной линии доверия A→B движется **искра** (ядро `sparkTxCore` + мягкий шлейф `sparkTxTrail`), скорость движения ниже «кометы», чтобы эффект читался;
  - во время движения ребро становится **ярким и мерцающим** (active link), подсвечиваясь цветом искры;
  - при достижении узла B:
    - узел B коротко **вспыхивает** (не «взрыв»);
    - появляется лейбл суммы `N GC`, который улетает вверх и растворяется.

### 10.3. Клиринг

Требование: показать «схлопывание долга» как взаимозачёт (netting) без агрессивной «взрывной» метафоры.

- эвристика Этапа 1:
  - попытаться найти треугольник (A-B-C-A) в текущем графе;
  - если не найден — использовать локальную эвристику вокруг выбранного узла.
- визуал (Canvas):
  - по выбранной линии доверия запускаются **две искры разных цветов**:
    - `sparkDebt` (orange) и `sparkCredit` (cyan);
  - искры движутся **навстречу** и встречаются в центре (midpoint);
  - в точке встречи:
    - короткая **вспышка**;
    - **рассыпание** мелкими искрами (`ParticleDust`);
    - (опционально) мягкий кольцевой импульс (`Shockwave` как pulse ring);
    - появляется лейбл суммы долга `N GC`, который улетает вверх и растворяется.
  - во время прохождения искр линия доверия становится яркой/мерцающей; после эффекта возвращается в обычный `linkIdle`.
  - демо-обновление баланса (например, уменьшение `abs(balance)`) с отражением в карточке.

### 10.4. Конкуренция эффектов

- во время clearing кнопка `Run Clearing` disabled
- транзакции могут быть разрешены параллельно clearing только если это не ломает визуальную читаемость (в Этапе 1 рекомендуется блокировать Tx на время clearing)

---

## 11. Производительность и качество

Обязательные меры:

- Canvas DPR scaling (учет `devicePixelRatio`)
- минимизация создания объектов в render loop
- лимит частиц: `maxParticles`
- пауза рендера при hidden tab (через `visibilitychange`)
- контроль качества bloom (low/high)

Метрики отладки:

- FPS (скользящее среднее)
- counts: nodes, links, particles

---

## 12. План разработки (без оценок)

### 12.1. Подготовка каркаса

- создать структуру компонентов и composables, как в [docs/ru/simulator/frontend/archive/SPEC-GEO-Simulator-Phase1.md](docs/ru/simulator/frontend/archive/SPEC-GEO-Simulator-Phase1.md:40)
- поднять пустую сцену с canvas + HUD

Критерии приемки:

- отображается full-screen сцена
- рендер цикл живой, без утечек

### 12.2. Данные (API + fallback)

- реализовать `loadGraph()` в store
- добавить генерацию моков

Критерии приемки:

- при недоступности API граф создается из моков
- структура данных соответствует контракту

### 12.3. Физика и интеракции

- подключить d3-force
- добавить hit-test
- реализовать выбор узла + подсветку связей
- реализовать drag узлов + release

Критерии приемки:

- клик показывает карточку
- связи подсвечиваются корректно
- узел перетаскивается и после отпускания стабилизируется

### 12.4. Визуальная полировка

- фон (bokeh/stars)
- bloom на узлах
- active link glow
- HUD стили (glass)

Критерии приемки:

- визуал воспринимается как «игровой», не как админка

### 12.5. Анимации Tx и Clearing

- реализовать движок частиц
- добавить `Single Tx`
- добавить `Run Clearing`

Критерии приемки:

- эффекты читаемы, работают стабильно
- clearing имеет встречные искры + вспышку в центре + рассыпание искрами + «улетающий» лейбл суммы

### 12.6. Производительность и debug

- оптимизации и лимиты
- debug overlay

Критерии приемки:

- 60 FPS на 100+ узлах в режиме качества по умолчанию

---

## 13. Чеклист приемки Этапа 1

- Граф отрисовывается и стабилизируется
- Клик по узлу открывает карточку
- Связи выбранного узла подсвечиваются, остальные затемняются
- Узлы перетаскиваются и после отпускания продолжают жить в физике
- `Single Tx` запускает видимую транзакцию
- `Run Clearing` запускает видимый клиринг (схлопывание) с VFX
- Нет зависимости от pan/zoom

