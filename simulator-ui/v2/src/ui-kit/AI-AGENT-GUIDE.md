<!-- AI Agent Guide: UI-kit design-system -->
# UI-kit Design System Guide (for AI agent)

Цель: создавать и поддерживать **каноничные стили UI-оверлеев** симулятора без «зоопарка» библиотек: **Vue 3 + обычный CSS**, но с **тематизацией** и едиными примитивами.

Эта инструкция задаёт правила, по которым агент должен добавлять новые стили/компоненты и не плодить дубликаты.

## 0) Каноническая архитектура

Принцип слоёв: **tokens → primitives → composition**.

```mermaid
flowchart TD
  A[Vue templates in components] --> B[UI primitives classes ds-*]
  B --> C[Semantic tokens CSS variables ds-*]
  C --> D[Theme selectors data-theme]
  D --> C
```

### Источники правды в репозитории

- Tokens: [`designSystem.tokens.css`](simulator-ui/v2/src/ui-kit/designSystem.tokens.css:1)
- Primitives: [`designSystem.primitives.css`](simulator-ui/v2/src/ui-kit/designSystem.primitives.css:1)
- Demo usage: [`DesignSystemDemoApp.vue`](simulator-ui/v2/src/dev/DesignSystemDemoApp.vue:1)
- Overlay/window guardrails: [`docs/ru/simulator/frontend/docs/overlay-window-development-rules.md`](docs/ru/simulator/frontend/docs/overlay-window-development-rules.md)

### Где смотреть изменения визуала (обязательно)

Да, ссылка на демо-страницу **должна** быть в гайде: это дисциплинирует агента и предотвращает расползание стилей.

Каноничная витрина примитивов и тем:

- HTML entry: [`design-system-demo.html`](simulator-ui/v2/design-system-demo.html:1)
- Vue entry: [`design-system-demo.ts`](simulator-ui/v2/src/design-system-demo.ts:1)

Правило: любой новый примитив или изменение токенов **обязательно отражать** в этой демо-странице.

Если изменение касается `WindowShell`, window-manager sizing/layout policy или overlay family contract, агент обязан дополнительно свериться с [`docs/ru/simulator/frontend/docs/overlay-window-development-rules.md`](docs/ru/simulator/frontend/docs/overlay-window-development-rules.md). Этот guide не заменяет тот документ, а покрывает только DS layer.

## 1) Главные правила (нельзя нарушать)

1. **Vue-компоненты не задают «визуал» напрямую**
   - Запрещено: хардкод цветов, теней, blur, градиентов в scoped CSS внутри компонентов.
   - Разрешено: только layout-частности конкретного компонента (позиционирование, гриды), и то по возможности через утилитарные layout-классы.

2. **Компоненты используют только примитивы**
   - В темплейтах используем классы `ds-*` (panel/button/input/badge и т.д.).
   - Никаких альтернативных параллельных наборов `.hud-*`, `.panel__*` и т.п. внутри SFC.

3. **Примитивы используют только semantic tokens**
   - Примитивы НЕ должны зависеть от конкретной темы.
   - Допускаются theme-override блоки вида `[data-theme='hud'] .ds-btn { ... }`, но внутри них всё равно опираться на токены: `var(--ds-accent)` и т.д.

4. **Тема меняет только значения токенов**
   - В идеале темы переопределяют переменные в [`designSystem.tokens.css`](simulator-ui/v2/src/ui-kit/designSystem.tokens.css:8)
   - Примитивы и композиции остаются прежними.

5. **Один компонент = один визуальный язык**
   - Если нужен новый элемент управления, сначала пытаемся выразить его существующими примитивами.
   - Если не получается, добавляем новый примитив (см. раздел 4).

## 2) Механика тематизации

Тема задаётся атрибутом на корневом контейнере приложения (или demo-страницы):

- `data-theme = saas | library | hud`
- `data-density = comfortable | compact`
- `data-motion = full | reduced`

Смысл:

- `data-theme` переключает визуальный язык через переопределение токенов в [`designSystem.tokens.css`](simulator-ui/v2/src/ui-kit/designSystem.tokens.css:79)
- `data-density` меняет масштабы отступов/высоты контролов (переменные `--ds-space-*`) в [`designSystem.tokens.css`](simulator-ui/v2/src/ui-kit/designSystem.tokens.css:64)
- `data-motion` выключает анимации, обнуляя длительности `--ds-dur-*` в [`designSystem.tokens.css`](simulator-ui/v2/src/ui-kit/designSystem.tokens.css:72)

## 3) Словарь токенов (semantic tokens)

Добавлять новые токены можно, но строго по правилам.

### Базовые группы (уже есть)

См. [`designSystem.tokens.css`](simulator-ui/v2/src/ui-kit/designSystem.tokens.css:8)

- Typography: `--ds-font-sans`, `--ds-font-mono`
- Geometry: `--ds-radius-*`, `--ds-cut` (HUD)
- Spacing: `--ds-space-1..4`
- Motion: `--ds-ease`, `--ds-dur-*`
- Surfaces: `--ds-surface-0..2`
- Text: `--ds-text-1..3`
- Borders: `--ds-border`
- Shadows: `--ds-shadow`, `--ds-shadow-soft`, `--ds-inner-highlight`
- Accents: `--ds-accent`, `--ds-accent-2`, `--ds-ring`
- Status colors: `--ds-ok`, `--ds-warn`, `--ds-err`, `--ds-info`
- FX: `--ds-glow`, `--ds-glow-strong`, `--ds-scanline`

### Правила добавления нового токена

1. Токен должен быть **semantic**, а не «сырым»
   - Хорошо: `--ds-surface-3`, `--ds-border-danger`, `--ds-focus-ring`
   - Плохо: `--ds-cyan-500`, `--ds-rgba-0-229-255-0-18`

2. Токен должен быть нужен **минимум двум примитивам**
   - Иначе это частный случай и должен жить в конкретном примитиве (но всё равно через existing tokens).

3. Каждая тема должна иметь осмысленное значение
   - Если новый токен введён, добавь его переопределение минимум в `saas`, `library`, `hud` (даже если значение совпадает).

## 4) Примитивы (каноничные UI элементы)

Примитив = переиспользуемый строительный блок UI (класс `ds-*`) из [`designSystem.primitives.css`](simulator-ui/v2/src/ui-kit/designSystem.primitives.css:1).

### Правила примитивов

1. Примитивы отвечают за:
   - формы
   - состояния `hover/focus/active/disabled`
   - размерность/плотность
   - доступность фокуса (focus ring)

2. Примитивы НЕ отвечают за:
   - позиционирование конкретных панелей HUD
   - бизнес-логику
   - уникальные одноразовые эффекты

3. Именование

- Префикс всегда `ds-`
- Модификаторы: `ds-btn ds-btn--primary`
- Элементы: `ds-panel__header` (допустимо)

### Канонические примитивы (минимальный набор)

- Surface: `ds-panel`, `ds-panel--elevated`, `ds-subpanel`
- Buttons: `ds-btn`, `ds-btn--primary|secondary|danger|ghost|icon`
- Inputs: `ds-input`, `ds-select`, `ds-field`, `ds-label`
- Status: `ds-badge ds-badge--ok|warn|err|info`, `ds-alert ds-alert--ok|err`
- Data: `ds-node-card` и его элементы
- Progress: `ds-progress__track`, `ds-progress__bar`

### Канонические overlay composition primitives

- `ds-ov-bar`: compact HUD-like surface shell; позиционирование остаётся у consumer layer.
- `ds-ov-metric`: compact metric chip для `TopBar`, `BottomBar`, `SystemBalanceBar`; это shared primitive, а не runtime surface.
- `ds-ov-dropdown`: bounded-intrinsic dropdown shell для HUD/details меню.
- `ds-ov-toast`: shared notification-toast shell; bottom offset и clamp читаются только из DS tokens.
- `ds-ov-message`: message/info overlay shell.
- `ds-ov-panel--compact`: compact interact-panel padding modifier for WM-managed panels.
- `ds-controls__row--compact`: compact form-row modifier for interact-panel field rows.
- `ds-controls__suffix`: shared bounded suffix/input row for compact interact-panel amount/limit controls.

Правило: `HudBar`, `ds-ov-bar` и `ds-ov-metric` не заносятся в runtime surface catalog как отдельные surfaces. Это composition primitives, которыми пользуются runtime surfaces.
Правило: `ds-ov-panel--compact`, `ds-controls__row--compact` и `ds-controls__suffix` относятся только к compact interact-form contract и не переиспользуются как generic HUD toolbar sizing fix.

## 5) Как добавить новый примитив (процедура)

Шаги для агента:

1. Определи, что именно не выражается текущими примитивами.
2. Проверь, нет ли похожего класса в [`designSystem.primitives.css`](simulator-ui/v2/src/ui-kit/designSystem.primitives.css:1).
3. Если примитива нет — добавь:
   - базовый стиль (не привязанный к теме)
   - состояния `hover/focus/active/disabled`
   - при необходимости: theme overrides блоки `[data-theme='hud'] ...`
4. Если нужен новый токен — добавь его в [`designSystem.tokens.css`](simulator-ui/v2/src/ui-kit/designSystem.tokens.css:8) и переопредели во всех темах.
5. Обнови demo, чтобы примитив был виден и сравним (см. [`DesignSystemDemoApp.vue`](simulator-ui/v2/src/dev/DesignSystemDemoApp.vue:1)).

## 6) Как делать «HUD стиль», не ломая архитектуру

HUD — это **не отдельный CSS зоопарк**, а:

1) HUD-тема токенов в [`designSystem.tokens.css`](simulator-ui/v2/src/ui-kit/designSystem.tokens.css:129)
2) HUD-оверрайды примитивов в [`designSystem.primitives.css`](simulator-ui/v2/src/ui-kit/designSystem.primitives.css:120)

Что допустимо в HUD overrides:

- `clip-path`/угловатая геометрия
- scanline overlay
- monospace для labels/section labels
- glow на hover/focus

Что недопустимо:

- новый параллельный набор классов (например `.hud-btn2`)
- копипастить целые панели в scoped CSS компонентов

### 6.1 Runtime surface catalog

| Runtime surface | Family | Sizing mode | Positioning owner | Width owner | Height owner | Z-layer token |
|---|---|---|---|---|---|---|
| WM interact window | `interact-panel` | `fixed-width-auto-height` | WindowManager | WM policy | measured/fallback | `--ds-z-panel` within WM stack |
| WM inspector window | `inspector-card` | `fixed-width-auto-height` or `bounded-intrinsic` | WindowManager | WM policy | measured/fallback | `--ds-z-panel` within WM stack |
| Top HUD stack | `hud-bar` | `stretch` | root top stack | stack container | content/min-row token | `--ds-z-top` |
| Bottom HUD stack | `hud-bar` | `stretch` | root bottom stack | stack container | content/min-row token | `--ds-z-bottom` |
| TopBar Advanced dropdown | `hud-dropdown` | `bounded-intrinsic` | details/dropdown shell | dropdown token contract | dropdown token contract | `--ds-z-inset` |
| TopBar Admin dropdown | `hud-dropdown` | `bounded-intrinsic` | details/dropdown shell | dropdown token contract | dropdown token contract | `--ds-z-inset` |
| BottomBar Artifacts dropdown | `hud-dropdown` | `bounded-intrinsic` | details/dropdown shell | dropdown token contract | dropdown token contract | `--ds-z-inset` |
| BottomBar DevTools dropdown | `hud-dropdown` | `bounded-intrinsic` | details/dropdown shell | dropdown token contract | dropdown token contract | `--ds-z-inset` |
| Toast | `notification-toast` | `intrinsic` | bottom stack offset | DS toast clamp tokens | content | `--ds-z-alert` |
| InteractHistoryLog | `bottom-overlay` | `intrinsic` | root bottom overlay | content | content | `--ds-z-bottom` |
| DevPerfOverlay | `dev-overlay` | `intrinsic` | fixed corner | DS overlay max-width contract | content/max-height token | `--ds-z-dev` |
| EdgeTooltip | `tooltip` | `intrinsic` | cursor-following shell | content | content | `--ds-z-tooltip` |
| Canvas labels/floating labels | `canvas-overlay` | `stretch` | viewport | viewport | viewport | `--ds-z-world-labels` |

Validation outcome for current shipped scope:

- Runtime code source of truth for the family matrix lives in `src/ui-kit/overlaySurfaceCatalog.ts`; this table must stay aligned with that file.
- `EdgeTooltip` micro-layout lives in `designSystem.overlays.css`; the component only binds content and placement style.
- `DevPerfOverlay` spacing/layout lives in `designSystem.overlays.css`; the component keeps only diagnostics/copy logic.
- `InteractHistoryLog`, toasts, HUD dropdowns, `DevPerfOverlay`, `EdgeTooltip`, and canvas overlays all sit inside the current runtime family table and z-layer contract.
- Inspector extraction remains deferred: `NodeCardOverlay` stays on the `ds-ov-node-card` + `ds-node-card` contract, while `EdgeDetailPopup` stays on the `ds-ov-edge-detail` quick-info/action contract. No shared `ds-inspector-row` primitive is introduced until a repeated rail contract appears.
- Compact interact-form rail proof lives in `src/components/compactOverlayFormRails.test.ts`; if a future form change reintroduces local width clamps, that file must be updated together with the DS contract.
- Browser-level overlap proof for passive tooltip vs interactive WM shell lives in `e2e/manual-operations-interact.spec.ts` (`C3:` test). If pointer/wheel routing changes, update that proof instead of relying only on DOM-level mocks.

### 6.1a Geometry publishing and sizing notes

- Top and bottom HUD stack geometry are measured/published at the root shell; `--ds-hud-stack-height` and `--ds-hud-bottom-stack-height` remain fallback tokens only.
- `fixed-width-auto-height` WM families keep width policy-owned by WindowManager; shell measurement may update height, but must not widen interact windows.
- `WindowShell` retains `queueMeasured()` plus `setTimeout(16)` as the approved ResizeObserver coalescing path; unmount must clear pending measurement publication.
- `ds-ov-panel` is visual-first; legacy absolute positioning is isolated behind compatibility classes and must not leak back into WM-managed consumers.

### 6.2 Runtime z-layer map

Source of truth stays in `App.css`; composition consumers in `designSystem.overlays.css` must reference these tokens instead of literals.

| Token | Role |
|---|---|
| `--ds-z-world-labels` | world labels and transient canvas labels above canvas, below UI overlays |
| `--ds-z-bottom` | bottom HUD stack and bottom runtime overlays |
| `--ds-z-top` | top HUD stack |
| `--ds-z-panel` | WM-managed inspector/panel surfaces |
| `--ds-z-dev` | dev overlays and floating diagnostics |
| `--ds-z-tooltip` | tooltip surfaces |
| `--ds-z-inset` | dropdown/inset overlays above bars but below alerts |
| `--ds-z-alert` | notification toasts and alert-level overlays |

### 6.3 Diagnostics and degraded behavior

- Dev-only overlay diagnostics are expected for CSS-token fallback, invalid measured size, stale publish, broken clamp input, and z-layer mismatch.
- Safe degraded behavior is mandatory: invalid geometry input must fall back or be ignored instead of crashing or silently corrupting runtime placement.
- Diagnostics live in the shared overlay/window pipeline, not in per-panel business components.

## 7) Чеклист качества для PR от агента

Перед завершением работ агент обязан проверить:

- Нет новых «магических» цветов в компонентах (всё через токены)
- Нет дублирования панелей/кнопок в scoped CSS
- Новые примитивы покрыты темами `saas|library|hud`
- Фокус видим (`:focus-visible` с ring)
- `data-motion='reduced'` отключает тяжёлые анимации
- Demo страница обновлена и показывает новый примитив

## 8) Рекомендация: какой уровень детализации гайда держать

Лучший баланс (чтобы агент был эффективен и не устроил рефакторинг ради рефакторинга):

1) **В этом гайде держать правила и канон** (уже сделано)
2) Добавить **короткий чеклист миграции + таблицу соответствий старых классов новым `ds-*`**
3) Примеры «до/после» держать **по 1–2 ключевых компонента**, чтобы агент понимал паттерн, но не копировал гигантские простыни.

Причина: в `simulator-ui/v2` сейчас много hand-crafted стилей в SFC. Если не дать агенту явную таблицу соответствий, он начнёт изобретать новые классы.

### 8.1 Таблица соответствий (шаблон)

Заполнять по мере миграции.

| Старое | Новое | Примечание |
|---|---|---|
| .panel | .ds-panel + .ds-panel--elevated | Surface-контейнер |
| .btn | .ds-btn | Выбрать модификатор (primary/secondary/ghost/danger) |
| .hud-input | .ds-input | Везде одинаковое поведение focus |
| .hud-select | .ds-select | Стрелка/appearance задаются примитивом |
| .hud-badge | .ds-badge | Статус через модификатор ok/warn/err/info |
| .hud-alert | .ds-alert | Варианты ok/err |

### 8.2 Чеклист миграции компонента

Для каждого Vue-компонента (например [`SimulatorAppRoot.vue`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:1)):

1. В темплейте заменить локальные классы на `ds-*` примитивы
2. Удалить дублирующиеся `scoped` стили, которые описывают внешний вид элементов управления
3. Оставить только layout-частности (grid/position), если их нельзя выразить примитивами
4. Если нужно — добавить отсутствующий примитив (раздел 5)
5. Проверить 3 темы (`saas|library|hud`) на [`design-system-demo.html`](simulator-ui/v2/design-system-demo.html:1)



