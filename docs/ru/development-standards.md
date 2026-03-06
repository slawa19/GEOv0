# Стандарты разработки GEO v0

## Введение

Этот документ описывает стандарты разработки проекта GEO v0. Он является единым источником правды для всех правил, конвенций и best practices.

Документ создан на основе анализа архитектурных ошибок, выявленных в ходе рефакторинга (март 2026), и обобщает существующие стандарты из [`docs/ru/06-contributing.md`](06-contributing.md), [`simulator-ui/v2/src/ui-kit/AI-AGENT-GUIDE.md`](../../simulator-ui/v2/src/ui-kit/AI-AGENT-GUIDE.md) и [`docs/ru/documentation-rules.md`](documentation-rules.md).

Компактная версия для AI-агента: [`.clinerules`](../../.clinerules) (читается автоматически при каждой задаче).

> **Связь с `.clinerules`**: Этот документ — полная версия стандартов. Сжатая версия для AI-агента находится в `.clinerules` (корень проекта). Нумерация разделов намеренно различается: `.clinerules` начинается с описания стека (§1), а этот документ — с антипаттернов (§1). При обновлении правил — обновляй ОБА файла.

---

## 1. Архитектурные антипаттерны: извлечённые уроки

В ходе рефакторинга (этапы E1–E11) и последующего анализа было выявлено и задокументировано 17 типов архитектурных ошибок. Каждый антипаттерн описан ниже с примерами кода и маркерами раннего обнаружения.

---

### A1. God Object — монолитный composable (useSimulatorApp)

**Описание:** Один composable берёт на себя слишком много зон ответственности: инициализацию, состояние, layout, рендер, события, персистенцию.

**Причина:** Удобство на старте — «всё в одном месте». При росте кода границы не проводятся.

**Решение:** Декомпозиция по SRP (Single Responsibility Principle). Фасад-composable выполняет только wiring — соединяет специализированные composables.

```ts
// ❌ ЗАПРЕЩЕНО — один composable делает всё
export function useSimulatorApp() {
  // layout
  const nodePositions = ref<...>()
  // render
  const canvas = ref<HTMLCanvasElement>()
  // events
  onMounted(() => { /* bootstrap */ })
  watch(nodePositions, () => { /* redraw */ })
  watch(canvas, () => { /* init renderer */ })
  // persistence
  localStorage.setItem('prefs', JSON.stringify(prefs))
  // ... 400+ строк
}

// ✅ ПРАВИЛЬНО — фасад соединяет специализированные модули
export function useSimulatorApp() {
  const layout = useLayoutEngine()
  const renderer = useCanvasRenderer(layout.nodePositions)
  const prefs = usePersistedSimulatorPrefs()
  const events = useSimulatorEvents({ layout, renderer })
  return { layout, renderer, prefs, events }
}
```

**🔍 Маркеры раннего обнаружения:**
- Файл composable > 150 строк
- Более 3 `watch()` или `onMounted()` в одном composable
- Composable импортирует >5 разных доменных модулей
- Название типа `useApp`, `useMain`, `useRoot` без уточнения домена
- Компонент с >15 props (кандидат на provide/inject или composition)
- Props forwarding >10 свойств без обработки → извлечь в context

---

### A2. Temporal Coupling — зависимость от порядка инициализации

**Описание:** Переменная захватывается в closure до того, как она инициализирована. Callback, созданный в момент `A`, захватывает ссылку, которая станет актуальной только в момент `B > A`.

**Причина:** JavaScript closure захватывает переменную по ссылке. Если callback создаётся до присвоения значения переменной — он работает с `undefined`.

**Решение:** Паттерн «replaceable getter» / lazy wrapper.

```ts
// ❌ ЗАПРЕЩЕНО — closure capture до инициализации
let renderer: Renderer | null = null

const onClick = () => {
  renderer.draw() // renderer ещё null в момент создания onClick
}

renderer = createRenderer(canvas)

// ✅ ПРАВИЛЬНО — lazy getter, всегда берёт актуальное значение
let getRenderer = (): Renderer | null => null

const onClick = () => {
  getRenderer()?.draw() // вызывается в runtime, когда renderer уже есть
}

const renderer = createRenderer(canvas)
getRenderer = () => renderer
```

**🔍 Маркеры раннего обнаружения:**
- Переменная объявлена через `let`, используется в callback, присваивается позже
- Паттерн: `let x = null; const fn = () => x.method(); x = createX()`
- Ошибка `Cannot read properties of null` в runtime при первом вызове

---

### A3. Magic Numbers / Hardcoded Values — нарушение Design Tokens SSOT

**Описание:** Визуальные значения (цвета, размеры, z-index, отступы) прописаны напрямую в компонентах или JS-коде вместо использования design tokens.

**Причина:** Быстрое решение «в лоб»; незнание системы дизайн-токенов проекта.

**Решение:** Все визуальные значения — через `--ds-*` CSS custom properties. JS читает их через `readCssVar()`.

```ts
// ❌ ЗАПРЕЩЕНО — magic numbers в JS
const NODE_RADIUS = 24
const HUD_HEIGHT = 48
const Z_OVERLAY = 100

// ❌ ЗАПРЕЩЕНО — hardcoded в CSS
.node { width: 48px; background: #1a2b3c; }
```

```ts
// ✅ ПРАВИЛЬНО — JS читает из CSS-переменных
import { readCssVar, readOverlayGeometryPx } from '@/ui-kit/overlayGeometry'

const nodeRadius = readCssVar('--ds-node-radius-px') // @token-sync: --ds-node-radius-px
const hudHeight = readOverlayGeometryPx('hud-height')
```

```css
/* ✅ ПРАВИЛЬНО — CSS использует токены */
.node {
  width: var(--ds-node-size);
  background: var(--ds-color-node-bg);
}
```

**🔍 Маркеры раннего обнаружения:**
- Числа без комментария в JS: `48`, `100`, `0.3`, `#1a2b3c`
- CSS-свойства без `var(--ds-*)`
- Дублирование: одно и то же число в `.css` и `.ts`
- render/physics «магические» числа (damping, repulsion, alpha) без именованной константы
- FX-параметры (`particleCount`, `glowRadius`, `fadeMs`) без группировки по контексту

---

### A4. Duplicated Logic — нарушение DRY

**Описание:** Одна и та же utility-функция (форматирование, вычисление, трансформация) дублируется в нескольких файлах.

**Причина:** Разработчик не проверил существующие утилиты; скопировал из соседнего файла.

**Решение:** Общие утилиты — в `simulator-ui/v2/src/utils/*.ts`. Перед созданием — обязательная проверка существующих.

```ts
// ❌ ЗАПРЕЩЕНО — дублирование в useComponentA.ts и useComponentB.ts
// useComponentA.ts
function formatAmount(v: number) { return v.toFixed(2) }

// useComponentB.ts
function formatAmount(v: number) { return v.toFixed(2) } // копия!

// ✅ ПРАВИЛЬНО — единый модуль
// src/utils/formatAmount.ts
export function formatAmount(v: number): string { return v.toFixed(2) }
```

**🔍 Маркеры раннего обнаружения:**
- Поиск по имени функции находит >1 определения
- Идентичные функции в разных composables
- Один и тот же `computed()` вычисляется в 2+ composables — кандидат на выделение
- Паттерн confirm→arm→execute повторяется в нескольких местах без общего composable

---

### A5. CSS/JS Drift — рассинхронизация стилей

**Описание:** Одно и то же значение задано отдельно в CSS и в JS. При изменении одного второе не обновляется — возникает визуальный баг.

**Причина:** Отсутствие SSOT: разработчик прописал значение в CSS, а потом — аналогичное в JS-константе.

**Решение:** JS всегда читает из CSS-переменных. Если это невозможно — обязательная маркировка `// @token-sync`.

```ts
// ❌ ЗАПРЕЩЕНО — дублирование значения
// styles.css  →  --ds-hud-height: 48px;
// component.ts → const HUD_HEIGHT = 48  // может разойтись!

// ✅ ПРАВИЛЬНО — JS читает из CSS
const HUD_HEIGHT = readCssVar('--ds-hud-height-px') // @token-sync: --ds-hud-height
```

**🔍 Маркеры раннего обнаружения:**
- JS-константа с тем же значением, что CSS-переменная
- Числа в JS совпадают с числами в CSS без явной связи

---

### A6. Type Safety Holes — дыры в типизации

**Описание:** Использование `as any`, `as unknown as T` для обхода TypeScript-типизации. Или использование union типа `string | number | boolean` вместо discriminated union.

**Причина:** «Быстрое решение» ошибки компилятора.

**Решение:** Discriminated union, structural narrowing, явные type guards.

Это правило распространяется не только на runtime-код, но и на тесты. `any` в mock-объектах, DOM event literals, `mock.calls`, SSE payload fixtures и window/document stubs создаёт такие же слепые зоны, как и в production-коде: тест может оставаться зелёным, но перестаёт защищать контракт.

```ts
// ❌ ЗАПРЕЩЕНО
function processEvent(e: unknown) {
  const event = e as any
  event.payload.data // небезопасно!
}

// ✅ ПРАВИЛЬНО — discriminated union
type SimEvent =
  | { type: 'tick'; tickIndex: number }
  | { type: 'payment'; paymentId: string }

function processEvent(e: SimEvent) {
  if (e.type === 'tick') {
    // TypeScript знает: e.tickIndex существует
  }
}
```

**🔍 Маркеры раннего обнаружения:**
- `as any` в коде (кроме помечённых интеграций с внешними библиотеками)
- `as unknown as T` — всегда подозрительно
- Отключённые правила ESLint: `// eslint-disable-next-line @typescript-eslint/no-explicit-any`
- `isRef()` runtime check для различения типов — признак потери type safety
- Redundant `?? ''` / `?? 0` при типе, который гарантированно non-nullish
- `any` в тестовых mock-объектах, event builders, `mock.calls`, window/document stubs
- `any` как способ обойти несовпадение контракта между тестом и реальным API

---

### A7. Смешанные storage слои — ad-hoc localStorage

**Описание:** Composable напрямую вызывает `localStorage.getItem` / `localStorage.setItem` вместо использования единого storage adapter.

**Причина:** Быстрое добавление персистенции «на месте».

**Решение:** UI prefs → `usePersistedSimulatorPrefs`. Новые persisted-поля добавляются туда же.

```ts
// ❌ ЗАПРЕЩЕНО — ad-hoc localStorage в composable
export function useMyFeature() {
  const value = ref(localStorage.getItem('myKey') ?? 'default')
  watch(value, v => localStorage.setItem('myKey', v))
}

// ✅ ПРАВИЛЬНО — через единый storage adapter
import { usePersistedSimulatorPrefs } from '@/composables/usePersistedSimulatorPrefs'

export function useMyFeature() {
  const prefs = usePersistedSimulatorPrefs()
  // используем prefs.myKey
}
```

**🔍 Маркеры раннего обнаружения:**
- `localStorage.getItem` / `localStorage.setItem` вне `usePersistedSimulatorPrefs`
- `sessionStorage` вне специализированного composable
- `localStorage.setItem(key, '')` — семантически некорректно для "удаления"; нужен `removeItem`

---

### A8. Test Naming Mismatch — неправильное именование тест-файлов

**Описание:** Тест-файл называется иначе, чем тестируемый модуль, или использует неправильный суффикс.

**Причина:** Рефакторинг переименовал модуль, но тест-файл не переименован; или суффикс `.spec.ts` использован для unit-теста.

**Решение:** Строгое соответствие имён. `foo.ts` → `foo.test.ts` (unit), `foo.spec.ts` (e2e только).

```
// ❌ ЗАПРЕЩЕНО
useLayout.ts       → useLayoutTests.spec.ts  (неправильный суффикс и имя)
overlayGeometry.ts → geometry.test.ts        (имя не совпадает)

// ✅ ПРАВИЛЬНО
useLayout.ts       → useLayout.test.ts
overlayGeometry.ts → overlayGeometry.test.ts
LoginPage.vue      → LoginPage.spec.ts       (e2e)
```

**🔍 Маркеры раннего обнаружения:**
- `*.spec.ts` в директории `src/` (не в `e2e/`)
- Имя тест-файла не совпадает с именем тестируемого модуля

---

### A9. Heavy Test Dependencies — тяжёлые зависимости в unit-тестах

**Описание:** Unit-тест импортирует модуль, который транзитивно тянет canvas, WebGL, DOM API — тест падает в Node.js окружении Vitest.

**Причина:** Логика (sizing, fill, mapping) не отделена от render-модуля; “чистые” хелперы и типы импортируются из тяжёлых модулей, которые тянут canvas/DOM через транзитивные зависимости.

**Решение:** Pure-функции выносятся в отдельные модули без зависимости от canvas/WebGL. 

Практическое правило для simulator-ui/v2:
- `render/nodePainter.ts` — только рисование (canvas) и связанные эффекты.
- “Чистые” хелперы — отдельно (например `render/nodeFill.ts`, `render/nodeSizing.ts`).
- Типы `LayoutNode`/`LayoutLink` — из `types/layout`, а не из `nodePainter`.

```ts
// ❌ ЗАПРЕЩЕНО — unit-тест тянет canvas
import { computeLayout } from '@/render/canvasRenderer' // внутри — new Canvas()

// ❌ ЗАПРЕЩЕНО — логика/типы тянутся из тяжёлого render-модуля
import { fillForNode, type LayoutNode } from '@/render/nodePainter'

// ✅ ПРАВИЛЬНО — pure-функция в отдельном модуле
// src/utils/layoutComputation.ts — нет зависимости от canvas
export function computeLayout(nodes: Node[]): Layout { ... }

// src/render/canvasRenderer.ts — использует computeLayout, но сам тяжёлый
import { computeLayout } from '@/utils/layoutComputation'

// ✅ ПРАВИЛЬНО — хелперы и типы импортируются из лёгких модулей
import { fillForNode } from '@/render/nodeFill'
import type { LayoutNode } from '@/types/layout'
```

**🔍 Маркеры раннего обнаружения:**
- Unit-тест падает с `ReferenceError: HTMLCanvasElement is not defined`
- Тест импортирует файл из `src/render/` напрямую

---

### A10. Dead Code — мёртвый код

**Описание:** Объявленные, но неиспользуемые переменные, функции, импорты остаются в кодовой базе.

**Причина:** Рефакторинг удалил использования, но не объявления; или код «на будущее».

**Решение:** Удалять неиспользуемый код сразу. ESLint `no-unused-vars: error`.

```ts
// ❌ ЗАПРЕЩЕНО
import { unusedHelper } from '@/utils/helper' // не используется
const DEBUG_FLAG = true // нигде не читается

export function useFeature() {
  const _legacyState = ref(null) // устаревшее, не используется
}

// ✅ ПРАВИЛЬНО — только используемый код
export function useFeature() {
  const activeState = ref<State | null>(null)
  return { activeState }
}
```

**🔍 Маркеры раннего обнаружения:**
- ESLint предупреждение `no-unused-vars`
- Импорты, не используемые в файле
- Переменные с префиксом `_` (кроме явно допустимых паттернов destructuring)

---

### A11. Orphaned Resources — утечка module-level state

**Описание:** Module-level кэши (`fxRenderer`, `glowSprites`, `window.__geoSim`) не очищаются при unmount → утечки памяти, stale state при hot-reload.

**Причина:** Кэш создаётся на уровне модуля для производительности, но cleanup-путь не предусмотрен.

**Решение:** Каждый module-level кэш должен предоставлять публичный `reset()` API, который вызывается в `onUnmounted`.

```ts
// ❌ ЗАПРЕЩЕНО — module-level cache без reset()
const gradientCache = new Map<string, CanvasGradient>();
export function getGradient(key: string) { /* ... */ }
// При hot-reload кэш сохраняет stale значения
```

```ts
// ✅ ПРАВИЛЬНО
const gradientCache = new Map<string, CanvasGradient>();
export function getGradient(key: string) { /* ... */ }
export function resetGradientCache() { gradientCache.clear(); }

// В компоненте:
onUnmounted(() => { resetGradientCache(); });
```

**🔍 Маркеры раннего обнаружения:**
- `new Map()`/`new Set()` на уровне модуля без `export function reset`
- `window.__*` без cleanup
- Module-level переменные, которые не сбрасываются между lifecycle-циклами

---

### A12. Hot-Path Allocations — аллокации в render-цикле

**Описание:** Создание объектов в per-frame циклах (60fps) приводит к GC-паузам и дроп-фреймам. Пример: 6000 gradient allocations/sec для 100-нодной сцены.

**Причина:** Естественный стиль кода — создавать нужные объекты по месту использования.

**Решение:** Object pooling, LRU-кэш, ring-buffer для hot-path данных.

```ts
// ❌ ЗАПРЕЩЕНО — аллокация каждый кадр
function renderFrame(nodes: Node[]) {
  for (const node of nodes) {
    const pos = { x: node.__x, y: node.__y }; // аллокация каждый кадр!
    const gradient = ctx.createLinearGradient(...); // аллокация!
    drawNode(pos, gradient);
  }
}
```

```ts
// ✅ ПРАВИЛЬНО — reusable objects + LRU-кэш
const posPool = { x: 0, y: 0 }; // reusable object
const gradientCache = new LRUCache<string, CanvasGradient>(128);

function renderFrame(nodes: Node[]) {
  for (const node of nodes) {
    posPool.x = node.__x; posPool.y = node.__y;
    const gradient = gradientCache.getOrCreate(key, () => ctx.createLinearGradient(...));
    drawNode(posPool, gradient);
  }
}
```

**🔍 Маркеры раннего обнаружения:**
- `new`, `{...}`, `[...]`, `createLinearGradient` внутри `for` цикла в файлах `render*.ts`, `physics*.ts`
- Performance profiler показывает GC-паузы > 5ms при 60fps

---

### A13. Argument Mutation — мутация аргументов функции

**Описание:** Функция мутирует переданный config/options объект → caller не ожидает side-effect и получает неконсистентное состояние.

**Причина:** Удобный способ «обновить» несколько полей за раз без return.

**Решение:** Либо возвращать новый объект, либо явно декларировать мутацию в имени/типе.

```ts
// ❌ ЗАПРЕЩЕНО — мутация чужого объекта
function updateViewport(config: ViewportConfig) {
  config.width = Math.max(config.width, 100); // мутация!
}
```

```ts
// ✅ ПРАВИЛЬНО — immutable return
function updateViewport(config: Readonly<ViewportConfig>): ViewportConfig {
  return { ...config, width: Math.max(config.width, 100) };
}
```

**🔍 Маркеры раннего обнаружения:**
- `param.field = ...` в функциях, не имеющих `out`/`mut` в имени
- Функция принимает объект и ничего не возвращает, но меняет поля

---

### A14. Crash on Data Inconsistency — падение при несогласованных данных

**Описание:** `throw` при dangling link или missing node из SSE → весь UI падает при временной рассинхронизации данных.

**Причина:** Defensive programming через exception — корректен для programmer-ошибок, но не для data race.

**Решение:** Graceful degradation: log + skip. `throw` только для нарушений инвариантов.

```ts
// ❌ ЗАПРЕЩЕНО — crash при data race
function resolveLink(link: Link) {
  const source = nodesMap.get(link.source);
  if (!source) throw new Error(`Node not found: ${link.source}`); // весь UI падает!
}
```

```ts
// ✅ ПРАВИЛЬНО — graceful skip
function resolveLink(link: Link) {
  const source = nodesMap.get(link.source);
  if (!source) {
    console.warn(`[resolveLink] dangling link: source=${link.source}, skipping`);
    return null;
  }
  return source;
}
```

**🔍 Маркеры раннего обнаружения:**
- `throw` в файлах, обрабатывающих данные из SSE, API, layout engine
- Отсутствие null-check перед обращением к Map/объекту из внешних данных

---

### A15. Deep Reactive on Hot Data — глубокая реактивность на hot-path

**Описание:** `reactive()` создаёт рекурсивные Proxy → O(N) overhead при 60fps мутациях layout nodes. Для 1000 узлов — заметные паузы рендера.

**Причина:** `reactive()` — дефолтный способ Vue для реактивного состояния; его накладные расходы неочевидны.

**Решение:** `shallowRef()` / `shallowReactive()` для данных, мутируемых в render/physics loop. `triggerRef()` для явного уведомления.

```ts
// ❌ ЗАПРЕЩЕНО — deep proxy для hot-path данных
const layoutNodes = reactive<LayoutNode[]>([]); // рекурсивный Proxy!
// В physics tick (60fps):
layoutNodes[i].__x = newX; // triggers deep proxy traversal!
```

```ts
// ✅ ПРАВИЛЬНО — shallow ref
const layoutNodes = shallowRef<LayoutNode[]>([]);
// В physics tick:
layoutNodes.value[i].__x = newX; // прямая мутация, без Proxy
triggerRef(layoutNodes); // явное batch-уведомление
```

**🔍 Маркеры раннего обнаружения:**
- `reactive()` для массивов/объектов, мутируемых в `requestAnimationFrame`/physics tick
- Performance profiler: >10% времени кадра в Proxy getter/setter

---

### A16. Stale Async Results — устаревший результат async-операции

**Описание:** Пользователь отменил операцию, но async-запрос уже в полёте → ответ приходит позже и перезаписывает актуальное состояние.

**Причина:** Нет механизма отмены; результат просто присваивается после await.

**Решение:** Epoch-counter или AbortController. Перед применением результата — проверка актуальности.

```ts
// ❌ ЗАПРЕЩЕНО — stale result обновляет state
async function startRun() {
  const result = await api.createRun();
  runState.value = result; // пользователь мог нажать Cancel!
}
```

```ts
// ✅ ПРАВИЛЬНО — epoch-based invalidation
let epoch = 0;
async function startRun() {
  const myEpoch = ++epoch;
  const result = await api.createRun();
  if (myEpoch !== epoch) return; // stale — discard
  runState.value = result;
}
function cancel() { epoch++; } // invalidate in-flight result
```

**🔍 Маркеры раннего обнаружения:**
- `await` + state assignment без epoch/AbortController check в composables с cancel/stop
- Состояние "подёргивается" при быстрых последовательных операциях

---

### A17. Degenerate Input Crash — падение на вырожденных входах

**Описание:** Viewport 0×0 при свёрнутом окне → все узлы коллапсируют в origin → при восстановлении граф "взрывается". Пустой массив или null ref → деление на ноль / NPE.

**Причина:** Guards для граничных случаев не написаны; код предполагает "разумные" входы.

**Решение:** Early return для degenerate inputs на входе функции. Unit-тесты для граничных значений.

```ts
// ❌ ЗАПРЕЩЕНО — нет guard
function fitToViewport(nodes: Node[], width: number, height: number) {
  const scaleX = width / bounds.width; // width=0 → Infinity!
  const scaleY = height / bounds.height;
  // узлы разлетаются в бесконечность
}
```

```ts
// ✅ ПРАВИЛЬНО — guard degenerate inputs
function fitToViewport(nodes: Node[], width: number, height: number) {
  if (width < 1 || height < 1 || nodes.length === 0) return; // skip degenerate
  const scaleX = width / bounds.width;
  const scaleY = height / bounds.height;
}
```

**🔍 Маркеры раннего обнаружения:**
- Деление на `width`/`height` без guard
- `nodes[0]` без `nodes.length` check
- cache-ключ использует только `nodes.length` (не уникален при изменении содержимого)

---

## 2. Правила кода

### 2.1 Python (backend)

Подробные правила: [`docs/ru/06-contributing.md`](06-contributing.md)

**Краткая выжимка:**
- Formatter: **Black**, `line-length = 100`
- Linter: **Ruff** (заменяет flake8 + isort)
- Типы: **mypy** в strict режиме
- Именование: snake_case для функций/переменных, PascalCase для классов
- Async: SQLAlchemy async sessions, `async def` для всех endpoint handlers
- Commits: Conventional Commits — `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`

### 2.2 TypeScript/Vue (frontend)

Подробные правила: [`simulator-ui/v2/src/ui-kit/AI-AGENT-GUIDE.md`](../../simulator-ui/v2/src/ui-kit/AI-AGENT-GUIDE.md)

- **TypeScript**: `strict: true` в tsconfig — без исключений
- **Vue 3**: Composition API + `<script setup lang="ts">` — Options API не использовать
- **Именование**:
  - Composable: `use{PascalCase}.ts` (например, `useLayoutEngine.ts`)
  - Component: `{PascalCase}.vue` (например, `NodeCardOverlay.vue`)
  - Utils: `{camelCase}.ts` в `src/utils/` (например, `formatAmount.ts`)
- **ESLint**: конфиг в `simulator-ui/v2/eslint.config.js`
- **Порог декомпозиции**: composable > 150 строк или > 3 `watch/onMounted` → декомпозировать

### 2.3 CSS / Design System

Подробные правила: [`simulator-ui/v2/src/ui-kit/AI-AGENT-GUIDE.md`](../../simulator-ui/v2/src/ui-kit/AI-AGENT-GUIDE.md)

- Design tokens: `--ds-*` custom properties в [`designSystem.tokens.css`](../../simulator-ui/v2/src/ui-kit/designSystem.tokens.css)
- Component styles: `<style scoped>` в Vue SFC
- MUST NOT: inline styles для визуальных свойств
- MUST NOT: `!important` без комментария-обоснования
- MUST NOT: значения без токенов (цвета hex, размеры px напрямую)

---

## 3. Правила тестирования

### 3.1 Backend: pytest

- Директория тестов: `tests/`
- Integration тесты: `tests/integration/test_*.py`
- Запуск: `pytest tests/` из корня проекта
- Фикстуры: в `conftest.py` на уровне директории
- Correlation ID: каждый тест должен логировать correlation ID для диагностики
- Артефакты: тесты симулятора сохраняют артефакты для post-run анализа

### 3.2 Frontend: Vitest (unit)

- Unit-тесты: `simulator-ui/v2/src/**/*.test.ts`
- Конфиг: `simulator-ui/v2/vite.config.ts` (секция `test`)
- Запуск: `npm run test:unit` из `simulator-ui/v2/`
- Изолированность: НЕ импортировать canvas/WebGL модули в unit-тестах
- Mock: использовать `vi.mock()` для тяжёлых зависимостей
- Type safety: тесты и mocks ДОЛЖНЫ соблюдать те же правила типизации, что и runtime-код
- MUST: typed builders/helpers для DOM events, window/document mocks, SSE payload fixtures, `mock.calls`
- MUST NOT: использовать `as any` для ускорения написания теста или «починки» несовпавшего mock-контракта
- SHOULD: если нужен мост к широкому DOM/API контракту, использовать один узкий `unknown` boundary с комментарием, а не распространять `any` по тесту

### 3.3 Frontend: Playwright (e2e)

- E2E тесты: `simulator-ui/v2/e2e/**/*.spec.ts`
- Запуск: `npm run test:e2e` из `simulator-ui/v2/`
- Именование: ТОЛЬКО `*.spec.ts` для e2e, НИКОГДА `*.spec.ts` в `src/`

### 3.4 Naming conventions

| Тип | Паттерн | Расположение |
|---|---|---|
| Unit (Vitest) | `*.test.ts` | `src/**/*.test.ts` |
| E2E (Playwright) | `*.spec.ts` | `e2e/**/*.spec.ts` |
| Backend (pytest) | `test_*.py` | `tests/**/*.py` |

### 3.5 Gate команды

```bash
# Frontend (из simulator-ui/v2/)
npm run typecheck    # TypeScript — MUST pass
npm run test:unit    # Vitest unit — MUST pass
npm run test:e2e     # Playwright — SHOULD pass (если затронуты e2e файлы)

# Backend (из корня проекта)
pytest tests/        # MUST pass
mypy app/            # MUST pass
ruff check app/      # MUST pass
```

---

## 4. Git & PR Process

Подробные правила: [`docs/ru/06-contributing.md`](06-contributing.md)

### 4.1 Conventional Commits

```
<type>(<scope>): <description>

feat(simulator): add layout decomposition composable
fix(overlay): correct z-index token reference
refactor(composables): extract useLayoutEngine from useSimulatorApp
test(unit): add overlayGeometry unit tests
docs(standards): update development-standards.md
chore(deps): update vitest to 1.x
```

Типы: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `perf`, `ci`

### 4.2 Branch naming

```
feat/simulator-layout-decomposition
fix/overlay-z-index-token
refactor/extract-layout-engine
test/add-overlay-geometry-tests
```

### 4.3 PR checklist (backend + frontend)

**Обязательно перед открытием PR:**
- [ ] `npm run typecheck` — pass
- [ ] `npm run test:unit` — pass
- [ ] `pytest tests/` — pass (если затронут backend)
- [ ] Нет `as any` без комментария-обоснования
- [ ] Тесты и mocks не обходят типизацию через `any`; узкие `unknown` bridges документированы
- [ ] Нет magic numbers — только `--ds-*` токены
- [ ] Нет дублированных утилит
- [ ] Тест-файлы именованы корректно
- [ ] Нет мёртвого кода
- [ ] CHANGELOG / ADR обновлён (если архитектурное изменение)

---

## 5. Документация

Подробные правила: [`docs/ru/documentation-rules.md`](documentation-rules.md)

### 5.1 Структура

```
docs/
  ru/           — документация на русском
    06-contributing.md
    development-standards.md (этот файл)
    documentation-rules.md
plans/
  INDEX.md      — индекс всех планов и ADR
  *.md          — планы, ADR, спецификации
simulator-ui/v2/src/ui-kit/
  AI-AGENT-GUIDE.md  — руководство по Design System
```

### 5.2 Статусы документов

- `[DRAFT]` — черновик, не финализирован
- `[ACTIVE]` — актуальный, применяется
- `[DEPRECATED]` — устаревший, не применять
- `[ARCHIVED]` — архивный, только для истории

### 5.3 Нейминг файлов документации

- Формат: `kebab-case.md`
- Дата в имени — только для versioned артефактов: `plan-2026-03-04.md`
- Стандарты и правила — без даты: `development-standards.md`

---

## 6. Чеклист для AI-агента (Quick Reference)

| Если ты делаешь... | Проверь... |
|---|---|
| Создаёшь composable | SRP: одна зона ответственности. Размер < 150 строк. Нет closure-capture переменных до их инициализации |
| Работаешь с CSS/JS значениями | Нет magic numbers. Все визуальные значения через `--ds-*`. JS читает через `readCssVar()`. Синхронные пары помечены `@token-sync` |
| Добавляешь утилиту | Проверил `src/utils/` на дубли? Один файл = одна утилита (или группа связанных) |
| Пишешь тест | Имя файла = имя модуля. Суффикс `.test.ts` для unit, `.spec.ts` для e2e. Нет тяжёлых render-зависимостей. Typed builders/helpers вместо `any` |
| Добавляешь persisted state | Используй `usePersistedSimulatorPrefs`, не `localStorage` напрямую |
| Делаешь рефакторинг | Малые шаги (одно логическое изменение). Gate до и после. Поведение сохранено |
| Пишешь TypeScript | No `as any` в runtime и тестах. Discriminated unions для variant-типов. Structural narrowing |
| Удаляешь / переименовываешь | Нет orphan тест-файлов. Нет мёртвого кода. Импорты обновлены |
| Добавляешь новый design token | Добавил в `designSystem.tokens.css`. Обновил `AI-AGENT-GUIDE.md` |
| Архитектурное решение | Записал ADR в `plans/` или `docs/`. Обновил `plans/INDEX.md` |

---

## Ссылки на связанные документы

- [`docs/ru/06-contributing.md`](06-contributing.md) — процесс разработки, git workflow, code style
- [`docs/ru/documentation-rules.md`](documentation-rules.md) — правила документирования
- [`simulator-ui/v2/src/ui-kit/AI-AGENT-GUIDE.md`](../../simulator-ui/v2/src/ui-kit/AI-AGENT-GUIDE.md) — Design System, токены, компоненты UI-kit
- [`plans/INDEX.md`](../../plans/INDEX.md) — индекс планов и архитектурных решений
- [`.clinerules`](../../.clinerules) — компактная версия этих правил для AI-агента
