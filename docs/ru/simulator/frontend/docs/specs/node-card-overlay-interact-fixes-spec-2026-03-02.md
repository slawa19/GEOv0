# NodeCardOverlay — Проблемы в Interact Mode и план исправлений

- **Дата**: 2026-03-02
- **Статус**: Draft
- **Компонент**: NodeCardOverlay (Interact mode, Simulator UI v2)

---

## Описание проблем

## Подтверждение актуальности (по коду на 2026-03-02)

Ниже — конкретные текущие факты из кода, подтверждающие причины из разделов P0–P2:

- `simulator-ui/v2/src/App.css`: `--ds-z-world-labels: 20;`, `--ds-z-panel: 42;`, `.labels-layer { z-index: var(--ds-z-world-labels); }`
- `simulator-ui/v2/src/components/SimulatorAppRoot.vue`: `.wm-layer` сейчас **без** `z-index` (и, соответственно, без явного локального stacking context)
- `simulator-ui/v2/src/components/WindowShell.vue`: z-index окна берётся из `instance.z` (`zIndex: String(props.instance.z)`)
- `simulator-ui/v2/src/composables/windowManager/useWindowManager.ts`: `focusCounter` инкрементится и присваивается в `win.z`, начиная с 1

Это означает: в WM-режиме окна получают z-index `1..N`, а слой лейблов имеет `z-index: 20`, поэтому лейблы оказываются выше окон.

### P0 — Лейблы узлов рендерятся ПОВЕРХ попапа (WM mode)

**Симптом:** Имена узлов графа (Дмитро, Наталя, Олена) отрисовываются поверх содержимого попапа NodeCardOverlay, делая его нечитаемым.

**Причина:** В WM mode `focusCounter` в `useWindowManager.ts:398` присваивает окнам z-index начиная с 1 и инкрементируя на 1 при фокусе. Слой лейблов `.labels-layer` имеет `z-index: var(--ds-z-world-labels)` = 20 (определён в `App.css:22`). Все WM-окна с z < 20 оказываются НИЖЕ лейблов. Контейнер `.wm-layer` в `SimulatorAppRoot.vue:1537` не задаёт z-index, поэтому окна конкурируют в общем stacking context.

В legacy режиме (wm=0) проблемы нет — попап получает `z-index: 42` через `designSystem.overlays.css:350`.

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

**Причина:** В `useNodeCard.ts:102` используется `const cardH = 240` — жёстко закодированная оценка высоты карточки. Фактическая высота с trustlines в Interact mode составляет 500–800px. Алгоритм clamp по Y: `clamp(y, pad, rect.height - pad - cardH)` использует заниженную cardH.

**Затронутые файлы:**
- `useNodeCard.ts`

**Связанная проблема (legacy):** в `useNodeCard.ts` размеры карточки сейчас частично рассинхронизированы с CSS:
- `useNodeCard.ts`: `cardW = 340`, `cardH = 240`
- `designSystem.overlays.css`: `.ds-ov-node-card { width: 320px; max-width: min(400px, calc(100vw - 24px)) }`

Даже если исправить только `cardH`, алгоритм всё равно будет считать ширину/высоту приближённо, а на узких вьюпортах реальная ширина может быть меньше из-за `max-width`.

---

### P2 — `.wm-layer` контейнер без stacking context

**Симптом:** Связано с P0 — окна внутри `.wm-layer` не изолированы в собственном stacking context.

**Причина:** `.wm-layer` в `SimulatorAppRoot.vue:1537` имеет `position: absolute`, но не задаёт `z-index`, поэтому дочерние `WindowShell` конкурируют в общем контексте наложения с другими элементами (лейблами, панелями).

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
- **Вариант C (preferred):** После рендеринга карточки использовать `getBoundingClientRect()` для получения реальной высоты и скорректировать позицию, если карточка выходит за viewport

**Дополнение:** в legacy-режиме этот же подход (измерение DOM) решает сразу две проблемы: (1) реальную высоту (`cardH`) и (2) реальную ширину (`cardW`), включая `max-width` на узких экранах.

### Fix 4 — Скрытие лейблов выделенного узла (UX improvement)

- При открытии попапа для узла — скрывать лейбл этого узла и соседних узлов, которые перекрываются карточкой
- Реализация: в `useLabelNodes.ts` добавить фильтрацию по `selectedNodeId` и optional — по boundingRect карточки

**Замечание:** после Fix 1 (z-index `.wm-layer`) лейблы физически уйдут под окна, поэтому Fix 4 становится purely-UX (уменьшение визуального шума), а не «исправлением читаемости».

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

- [`wm-post-refactor-ux-issues-spec-2026-03-01.md`](wm-post-refactor-ux-issues-spec-2026-03-01.md) — описывает WM-UX-1 (сплющенное окно) и WM-UX-2 (закрытие parent), z-index проблема НЕ описана
- [`wm-window-design-parity-with-legacy-spec-2026-03-01.md`](wm-window-design-parity-with-legacy-spec-2026-03-01.md) — описывает P0 «окно в окне» и клиппинг/overflow, z-index НЕ описан
