# Typography (Design System v2)

Цель: сделать типографику в реализации `simulator-ui/v2` визуально совместимой с референсом из [`plans/ui-style-comparison.html`](../../../plans/ui-style-comparison.html:1).

## 1) Типографические токены

Источник токенов: [`simulator-ui/v2/src/ui-kit/designSystem.tokens.css`](designSystem.tokens.css:1).

### Density (Compact / Comfortable)

Механизм: атрибут `data-density='compact'|'comfortable'` задаёт **только геометрию/ритм**, без изменения базовых размеров шрифта.

Реализовано через токены (см. [`designSystem.tokens.css`](designSystem.tokens.css:1)):

- Ритм/отступы: `--ds-space-1..4`.
- Высота контролов/кнопок:
  - `--ds-control-height` (база `--ds-control-height-base` + delta от density)
  - `--ds-button-height` (база `--ds-button-height-base` + delta от density)
- Горизонтальные паддинги:
  - `--ds-control-padding-x` (база + delta)
  - `--ds-button-padding-x` (база + delta)

Примитивы читают эти значения в [`designSystem.primitives.css`](designSystem.primitives.css:1) (например, `.ds-input`, `.ds-btn`).

### HUD casing (регистр)

Для консистентности HUD (без влияния на другие темы) регистр вынесен в токены:

- `--ds-typo-label-text-transform`
- `--ds-typo-panel-heading-text-transform`

HUD тема выставляет `uppercase`, остальные — `none`.

### Базовые семейства

- `--ds-font-sans` — базовый sans стек.
- `--ds-font-mono` — базовый mono стек.

### Root size / base rhythm

- `--ds-root-font-size` — размер базового `1rem` внутри `.ds-page`.
  - Референс: `html { font-size: 15px; }`.

### Semantic typography tokens

Токены ниже — «семантические» (то есть описывают назначение текста, а не конкретный компонент):

- Body / base text
  - `--ds-typo-body-font-family`
  - `--ds-typo-body-font-size`
  - `--ds-typo-body-line-height`
  - `--ds-typo-body-letter-spacing`
  - `--ds-typo-body-font-weight`

 - Section label (маленькие UPPERCASE подписи секций)
   - `--ds-typo-section-label-font-family`
   - `--ds-typo-section-label-font-size`
   - `--ds-typo-section-label-line-height`
   - `--ds-typo-section-label-letter-spacing`
   - `--ds-typo-section-label-font-weight`
   - `--ds-section-label-margin-bottom` (ритм/отступ снизу)

- Form label (лейбл поля)
  - `--ds-typo-label-font-family`
  - `--ds-typo-label-font-size`
  - `--ds-typo-label-line-height`
  - `--ds-typo-label-letter-spacing`
  - `--ds-typo-label-font-weight`

- Panel heading (заголовок панели/карточки)
  - `--ds-typo-panel-heading-font-family`
  - `--ds-typo-panel-heading-font-size`
  - `--ds-typo-panel-heading-line-height`
  - `--ds-typo-panel-heading-letter-spacing`
  - `--ds-typo-panel-heading-font-weight`

- Controls (input/select)
  - `--ds-typo-control-font-family`
  - `--ds-typo-control-font-size`
  - `--ds-typo-control-line-height`
  - `--ds-typo-control-letter-spacing`
  - `--ds-typo-control-font-weight`

 - Buttons
   - `--ds-typo-button-font-family`
   - `--ds-typo-button-font-size`
   - `--ds-typo-button-line-height`
   - `--ds-typo-button-letter-spacing`
   - `--ds-typo-button-font-weight`

 - Badge (пилюли статуса)
   - `--ds-typo-badge-font-family`
   - `--ds-typo-badge-font-size`
   - `--ds-typo-badge-line-height`
   - `--ds-typo-badge-letter-spacing`
   - `--ds-typo-badge-font-weight`
   - `--ds-typo-badge-text-transform`
   - `--ds-badge-height`
   - `--ds-badge-padding-y`
   - `--ds-badge-padding-x`

 - Alert (строка уведомления)
   - `--ds-typo-alert-font-family`
   - `--ds-typo-alert-font-size`
   - `--ds-typo-alert-letter-spacing`
   - `--ds-typo-alert-font-weight`
   - `--ds-alert-padding-y`
   - `--ds-alert-padding-x`
   - `--ds-alert-gap`
   - `--ds-alert-icon-font-size`

- Numeric (amounts)
  - `--ds-typo-numeric-font-family`
  - `--ds-typo-numeric-font-variant` (целевое значение: `tabular-nums`)

## 2) Маппинг: элемент → класс → токены → целевой вид

Реализация классов: [`simulator-ui/v2/src/ui-kit/designSystem.primitives.css`](designSystem.primitives.css:1).

| Элемент (в UI) | Класс (ds-*) | Основные токены | Целевой вид |
|---|---|---|---|
| База страницы / общий текст | `.ds-page` | `--ds-root-font-size`, `--ds-typo-body-*` | **shadcn:** sans + нейтральный ритм, **HUD:** mono-ритм (если задано темой) |
| Лейбл секции (UPPERCASE) | `.ds-section-label`, `.ds-kicker` | `--ds-typo-section-label-*` | **shadcn:** 0.7rem + tracking 0.08em, **HUD:** mono + uppercase |
| Лейбл поля | `.ds-label` | `--ds-typo-label-*` | **shadcn:** 0.8rem / weight 500, **HUD:** 0.7rem / weight 600 + tracking 0.08em |
| Заголовок панели | `.ds-h2` (используется в `.ds-panel__header`) | `--ds-typo-panel-heading-*` | **shadcn:** ~0.88rem / weight 600, **HUD:** mono (и, при необходимости, uppercase/spacing в теме) |
| Input / Select | `.ds-input`, `.ds-select` | `--ds-typo-control-*` | **shadcn:** 0.85rem, **HUD:** 0.82rem mono |
| Button | `.ds-btn` | `--ds-typo-button-*` | **shadcn:** 0.8rem weight 500, **HUD:** 0.75rem weight 600 + tracking 0.06em |
| Amount / числовые значения | `.ds-mono`, `.ds-node-card__balance` | `--ds-typo-numeric-*` | В обоих стилях: tabular-nums (табличные цифры), mono стек по теме |

## 3) Чеклист визуальной проверки

Ссылки:

- Референс: [`plans/ui-style-comparison.html`](../../../plans/ui-style-comparison.html:1)
- Реализация (dev): `simulator-ui/v2` (Vite)
- Внутренний демо-экран примитивов (если используется в проекте): `simulator-ui/v2/design-system-demo.html`

Проверяем глазами:

1. База страницы: общий текст выглядит как в референсе (sans для shadcn, mono-ритм для HUD, если включён).
2. `.ds-label` (лейблы полей) — совпадают размер/вес/межбуквие с колонкой shadcn; в HUD — компактнее, uppercase + tracking.
3. Заголовки панелей (`.ds-h2` в `.ds-panel__header`) — совпадают с референсом по размеру/весу.
4. Кнопки (`.ds-btn`) — размер/вес/line-height соответствуют; в HUD — uppercase + tracking.
5. Поля ввода (`.ds-input`/`.ds-select`) — размер текста и line-height совпадают.
6. Числа/amount (классы `.ds-mono` / `.ds-node-card__balance`) — табличные цифры (цифры не «прыгают» при изменении).
7. Number input spin buttons в Chromium: нет белого фона, стрелочки вписаны в тему.

## Shadcn fidelity notes

Точные значения, которыми «прибиваем» визуальное совпадение с shadcn-колонкой из [`plans/ui-style-comparison.html`](../../../plans/ui-style-comparison.html:1).

1) Section label / section headings (класс `.ds-section-label`, `.ds-kicker`)

- `font-size`: `0.7rem` (при `--ds-root-font-size: 15px` это ~`10.5px`)
- `font-weight`: `400`
- `letter-spacing`: `0.08em`
- `text-transform`: `uppercase`
- `color`: `--ds-text-3` = `#71717a`
- `margin-bottom`: `0.3rem`

2) Badges (класс `.ds-badge`)

- `font-size`: `0.73rem` (~`10.95px`)
- `font-weight`: `500`
- `letter-spacing`: `0`
- `text-transform`: `none` (сохраняем регистр текста как в контенте)
- `padding`: `0.1rem 0.5rem`

3) Alerts (класс `.ds-alert`)

- `font-size`: `0.8rem` (`12px`)
- `font-weight`: `400`
- `letter-spacing`: `0`
- `padding`: `0.5rem 0.75rem`
- `gap`: `0.5rem`
- иконка (`.ds-alert__icon`): `font-size: 1rem`

