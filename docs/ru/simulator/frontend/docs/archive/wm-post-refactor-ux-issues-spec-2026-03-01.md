# WM Post-Refactor UX Issues — spec (2026-03-01)

## Контекст
После рефакторинга окон (WindowManager + WindowShell) остались реальные UX-проблемы, которые **не считаются решёнными тестами**, т.к. проявляются в живом UI.

Связанные документы:
- `docs/ru/simulator/frontend/docs/specs/window-manager-acceptance-criteria.md` — базовые критерии/карта реализации

Цель этого документа: **зафиксировать симптомы, воспроизведение, ожидаемое поведение, гипотезы причин и варианты решений**.

## Scope / Non-goals

В scope:
- WM-UX-0: «Визуальный паритет WM-окон с Legacy» (без «окно-в-окне», без дубля `×`, без клиппинга legacy-эффектов)
- WM-UX-1: «Родительское Action окно сплющено» (визуальные размеры/паддинги/лейаут)
- WM-UX-2: «Родительское окно закрывается при открытии Send Payment»

Статус (по коду на 2026-03-02):
- WM-UX-1: ❓ требует визуального подтверждения (manual QA)
- WM-UX-2: ✅ RESOLVED (см. раздел WM-UX-2)

Non-goals (для этой итерации):
- Редизайн компонентов или изменение дизайн-системы
- Добавление новых окон/страниц/сложных анимаций
- Любые изменения бизнес-логики платежей/клиринга

## Определения
- «Родительское Action окно» — окно-инспектор, из которого запускается действие (например, Edge Detail / Node Card в режиме WM).
- «Дочернее окно» — Interact-панель (Manual Payment / Trustline / Clearing), которая открывается для выполнения действия.

---

## WM-UX-0 — Визуальный паритет WM с Legacy (P0)

### Симптом
В WM режиме окна визуально отличаются от Legacy и появляются регрессии:
- «Окно в окне» (двойная поверхность/рамка).
- Дублирование `×` (закрытие) — особенно заметно на NodeCard.
- Хедер/типографика отличается от Legacy.
- Ощущение “растянуто/сжато” из-за того, что оболочка навязывает `width/height`.
- Клиппинг/overflow: внешняя оболочка режет legacy-эффекты (особенно в HUD теме).

### Root cause (системно)
WM смешивает две конкурирующие модели:
- Legacy: компонент сам является surface (ds-panel/ds-ov-surface), сам рисует хедер и `×`.
- WM: оболочка (`WindowShell`) рисует surface+хедер+`×`, а контент должен стать “content-only”.

### Решение (зафиксировано)
Выбираем модель **WM = geometry only**:
- `WindowShell` в WM-режиме становится **прозрачным контейнером** (позиционирование, z-order/focus, измерение DOM, transition), без своего surface/хедера/`×`.
- Визуальный frame (surface/header/`×`) остаётся внутри legacy компонентов → 1:1 паритет.

### Acceptance Criteria (WM-UX-0)
AC0. В WM-режиме отсутствует «окно в окне» и дубль `×`.
AC1. HUD/shadcn темы выглядят 1:1 с Legacy для NodeCard/EdgeDetail/Interact-панелей.
AC2. Никакой внешний контейнер не режет legacy-углы/clip-path/эффекты.

---

## WM-UX-1 — Action окно «сплющено»

### Симптом
Родительское Action окно в WM режиме выглядит «сплющенным»: нарушены ожидаемые размеры, внутренние отступы, сетка элементов.

### Acceptance Criteria (WM-UX-1)
AC1. Родительское Action окно в WM имеет консистентные паддинги/типографику и не выглядит «сплющенным».
AC2. На узком viewport контент не сжимается “в кашу”: появляется предсказуемый scroll внутри окна.
AC3. В legacy runtime (исторически; удалён) визуальное поведение не ухудшается.

---

## WM-UX-2 — Родительское окно закрывается при Send Payment

**Статус:** ✅ RESOLVED (2026-03-02)

Решение (факт реализации):
- В `SimulatorAppRoot.vue` добавлена keepAlive-семантика для `edge-detail` при инициировании payment flow.
- При `Send Payment` edge-detail остаётся открытым как контекст, а поверх открывается interact-panel.

Трассировка:
- Код: `simulator-ui/v2/src/components/SimulatorAppRoot.vue` → `onEdgeDetailSendPayment()` + watcher inspector→WM (проверка `wmEdgeDetailKeepAlive`).
- Тест: `simulator-ui/v2/src/components/SimulatorAppRoot.interact.test.ts` → `wm=1: Send Payment from edge-detail keeps edge-detail window open (keepAlive)`.

### Acceptance Criteria (WM-UX-2)
AC1. В WM (по умолчанию) Edge Detail остаётся открыт при “Send Payment”, при этом Manual Payment открывается поверх.
AC2. ESC-policy работает по правилам: сначала закрывается interact окно, затем inspector.
AC3. В legacy режиме поведение не ухудшается.

---

## Решения, требующие ревью
Перед реализацией выбрать:
- WM-UX-0: подтверждение канона “WM = geometry-only (frameless)”
- WM-UX-1: Option A vs B vs C

WM-UX-2: ✅ решено (Option B — keepAlive) → ревью не требуется.