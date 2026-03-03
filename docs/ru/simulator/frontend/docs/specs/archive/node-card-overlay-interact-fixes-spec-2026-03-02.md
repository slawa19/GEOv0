# NodeCardOverlay — Проблемы в Interact Mode и план исправлений

- **Дата**: 2026-03-02
- **Статус**: Draft
- **Компонент**: NodeCardOverlay (Interact mode, Simulator UI v2)

---

## Описание проблем

## Подтверждение актуальности (по коду на 2026-03-02)

Ниже — конкретные текущие факты из кода, подтверждающие причины из разделов P0–P2:

- `simulator-ui/v2/src/App.css`: `--ds-z-world-labels: 20;`, `--ds-z-panel: 42;`, `.labels-layer { z-index: var(--ds-z-world-labels); }`
- `simulator-ui/v2/src/components/SimulatorAppRoot.vue`: `.wm-layer` имеет `z-index: var(--ds-z-panel)` (локальный stacking context для WM-окон)
- `simulator-ui/v2/src/components/WindowShell.vue`: z-index окна берётся из `instance.z` (`zIndex: String(props.instance.z)`)
- `simulator-ui/v2/src/composables/windowManager/useWindowManager.ts`: `focusCounter` инкрементится и присваивается в `win.z`, начиная с 1

Это означает: в WM-режиме окна получают z-index `1..N`, а слой лейблов имеет `z-index: 20`. Если `.wm-layer` НЕ имеет собственного `z-index` (историческое состояние до фикса), лейблы оказываются выше окон. При наличии `z-index` на `.wm-layer` (текущее состояние) проблема перекрытия должна быть снята.

## Статус реализации (факт по коду)

- Fix 1 (z-index `.wm-layer`): уже реализован в коде
- Fix 2 (max-height + scroll в trustlines): реализован
- Fix 3 (двухпроходный reclamp по реальному DOM-rect): уже реализован в коде
- Fix 4 (скрытие лейбла выбранного узла при открытой карточке): уже реализован в коде

### P0 — Лейблы узлов рендерятся ПОВЕРХ попапа (WM mode)

**Симптом:** Имена узлов графа (Дмитро, Наталя, Олена) отрисовываются поверх содержимого попапа NodeCardOverlay, делая его нечитаемым.

**Причина (исторически):** в WM mode `focusCounter` присваивает окнам z-index начиная с 1 и инкрементируя на 1 при фокусе. Слой лейблов `.labels-layer` имеет `z-index: var(--ds-z-world-labels)` = 20. Если `.wm-layer` не имеет собственного `z-index` (нет локального stacking context), все WM-окна с z < 20 оказываются ниже лейблов, и лейблы будут перекрывать попап.

**Текущий статус:** `.wm-layer` должен иметь `z-index: var(--ds-z-panel)` (локальный stacking context), поэтому симптом не должен воспроизводиться. Если воспроизводится — проверить, что это правило применилось и нет другого stacking context поверх WM-слоя.

В legacy режиме (исторически; legacy runtime удалён) проблемы не было — попап получал `z-index: 42` через `designSystem.overlays.css:350`.

**Затронутые файлы:**
- `useWindowManager.ts`
- `App.css`
- `SimulatorAppRoot.vue`

**Примечание (важно):** это проблема **не только** `NodeCardOverlay` — она затрагивает ВСЕ окна, которые рендерятся через `WindowShell` (например, `EdgeDetailPopup`, Interact-панели), т.к. общий механизм z-index один.

---

### P0 — Попап без ограничения высоты, контент уходит за viewport

**Симптом:** Список trustlines в попапе может содержать 10+ записей, карточка растягивается на 500+ пикселей и обрезается снизу экрана.

**Причина:** Ни `.ds-ov-node-card` в `designSystem.overlays.css:350`, ни `.nco-trustlines` в `NodeCardOverlay.vue:294` не имеют `max-height` или `overflow-y` свойств.

**Затронутые файлы:**
- `designSystem.overlays.css`
- `NodeCardOverlay.vue`

**Примечание (WM):** в frameless-режиме `WindowShell` не задаёт `overflow: auto` и старается не навязывать скролл/клиппинг, поэтому если сам контент (внутри `NodeCardOverlay`) не вводит внутренний скролл, большая карточка всё равно физически «вылезает» за пределы viewport.

---

### P1 — Алгоритм позиционирования недооценивает высоту карточки

**Симптом:** Попап размещается так, что нижняя часть гарантированно выходит за viewport.

**Причина (первый проход / fallback):** в базовом расчёте позиции (первый проход) используется `cardW=340` / `cardH=240` как оценка для выбора стороны и initial clamp. Это может дать неверный initial Y при больших trustlines.

**Текущий статус:** после первичной отрисовки реализован второй проход: на `nextTick()` читаются реальные размеры DOM через `getBoundingClientRect()` и применяются корректировки `dx/dy`, чтобы карточка не выходила за viewport (см. Fix 3).

**Затронутые файлы:**
- `useNodeCard.ts`

**Связанная проблема (legacy):** в `useNodeCard.ts` размеры карточки сейчас частично рассинхронизированы с CSS:
- `useNodeCard.ts`: `cardW = 340`, `cardH = 240`
- `designSystem.overlays.css`: `.ds-ov-node-card { width: 320px; max-width: min(400px, calc(100vw - 24px)) }`

Даже если исправить только `cardH`, алгоритм всё равно будет считать ширину/высоту приближённо, а на узких вьюпортах реальная ширина может быть меньше из-за `max-width`.

---

### P2 — `.wm-layer` контейнер без stacking context

**Симптом:** Связано с P0 — окна внутри `.wm-layer` не изолированы в собственном stacking context.

**Причина (исторически):** `.wm-layer` в `SimulatorAppRoot.vue` имел `position: absolute`, но без `z-index`, поэтому дочерние `WindowShell` конкурировали в общем контексте наложения с другими элементами (лейблами, панелями).

**Текущий статус:** в коде уже присутствует `z-index: var(--ds-z-panel)` на `.wm-layer`, что создаёт отдельный stacking context.

**Затронутые файлы:**
- `SimulatorAppRoot.vue`

---

## План исправлений

### Fix 1 — Z-index `.wm-layer` (решает P0 + P2)

- Добавить `.wm-layer` CSS свойство `z-index: var(--ds-z-panel)` (= 42) в `SimulatorAppRoot.vue`
- Это создаст stacking context для WM-окон и поместит их выше лейблов (z:20) и ниже тултипов (z:55)
- `focusCounter` внутри `.wm-layer` будет работать для порядка окон между собой (что корректно)
- **Альтернатива:** добавить новую CSS-переменную `--ds-z-wm-layer` со значением между 20 и 40

### Fix 2 — Max-height + scroll для списка trustlines (решает P0-overflow)

- Добавить в `.nco-trustlines` (`NodeCardOverlay.vue`): `max-height: calc(50vh - 120px)`, `overflow-y: auto`
- 120px — примерная высота header + action buttons карточки
- **Альтернативно:** задать `max-height` на `.ds-ov-node-card` с `overflow-y: auto` на контейнере

**Риск/деталь:** `calc(50vh - 120px)` на очень низких высотах viewport может стать ≤ 0. Чтобы карточка не «схлопывала» список до нуля, лучше использовать CSS clamp через `max()`, например: `max-height: max(140px, calc(50vh - 120px))`.

### Fix 3 — Динамическая высота cardH (решает P1)

- **Вариант A:** В `useNodeCard.ts` вычислять `cardH` динамически на основе количества trustlines: `cardH = 240 + trustlinesCount * 36`
- **Вариант B:** Если Fix 2 реализован (max-height на scroll), то cardH можно заменить на фиксированное значение, соответствующее max-height контейнера (например, `cardH = Math.min(realHeight, maxScrollHeight)`)

**Реализовано (вариант C):** после рендеринга используется `getBoundingClientRect()` и коррекция `left/top` через `dx/dy` на втором проходе (`nextTick()`). Для этого добавлен `cardRef` и проброшен до `NodeCardOverlay` через wiring.

**Дополнение:** в legacy-режиме этот же подход (измерение DOM) решает сразу две проблемы: (1) реальную высоту (`cardH`) и (2) реальную ширину (`cardW`), включая `max-width` на узких экранах.

### Fix 4 — Скрытие лейблов выделенного узла (UX improvement)

- При открытии попапа для узла — скрывать лейбл этого узла и соседних узлов, которые перекрываются карточкой
- Реализация: в `useLabelNodes.ts` добавить фильтрацию по `selectedNodeId` и optional — по boundingRect карточки

**Реализовано:** при открытой карточке скрывается лейбл выбранного узла (соседи остаются).

**Замечание:** после Fix 1 (z-index `.wm-layer`) лейблы физически уйдут под окна, поэтому Fix 4 — purely-UX (уменьшение визуального шума), а не «исправление читаемости».

---

## Дополнительные найденные проблемы/риски (связанные)

### P2 — Терминология направления trustline может вводить в заблуждение

В `TrustlineInfo` есть явное описание направления долга:
- `reverse_used`: «debt in reverse direction (debtor=from_pid, creditor=to_pid)»

Из этого следует, что базовое направление trustline: `from_pid` = creditor, `to_pid` = debtor.

В `NodeCardOverlay.vue` комментарии к группам сейчас содержат риск путаницы (например, «OUT trustlines (node = debtor)» при фильтрации по `from_pid === node.id`). Это не ломает UI напрямую, но повышает вероятность будущих логических ошибок (особенно в Interact Mode, где направление критично).

### P2 — Недостаточная тестовая защита для проблемы z-index

Сейчас есть тесты на «рендерится через WM слой», но нет проверки stacking порядка (например, что `.wm-layer` находится выше `.labels-layer`). Из-за этого регресс по z-index легко вернуть случайной правкой.

---

## Порядок реализации

1. **Fix 1** — P0, quick win, 1 строка CSS
2. **Fix 2** — P0, 2–3 строки CSS
3. **Fix 3** — P1, рефакторинг `useNodeCard.ts`
4. **Fix 4** — P2, UX improvement, опционально

---

## Связанные документы

- [`wm-post-refactor-ux-issues-spec-2026-03-01.md`](wm-post-refactor-ux-issues-spec-2026-03-01.md) — включает WM-UX-0/1/2 (визуальный паритет: «окно в окне», дубль `×`, клиппинг/overflow; а также сплющенное окно и закрытие parent). Z-index проблема НЕ описана.
- [`archive/wm-window-design-parity-with-legacy-spec-2026-03-01.md`](archive/wm-window-design-parity-with-legacy-spec-2026-03-01.md) — (архив) подробный список проблем и rationale для решения «WM = geometry-only».

