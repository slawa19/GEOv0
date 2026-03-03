# Legacy removal (WM-only) — план выпиливания legacy из runtime

> **Дата**: 2026-03-03  
> **Статус**: IMPLEMENTED (документ обновлён под текущее состояние репозитория)  
> **Область**: `simulator-ui/v2` (только фронтенд)  
> **Цель**: WM — единственный runtime-путь. Legacy UI — только статический референс по верстке (код компонентов + текстовые снапшоты HTML).

---

## 0) Контекст и мотивация

Сейчас в коде исторически сосуществуют два мира:
- **WM runtime** (WindowManager + WindowShell): окна управляются через `wm.open/close/reclamp`, есть measured sizing через `ResizeObserver`.
- **Legacy overlays**: окна/панели сами позиционируются относительно anchor, отдельная логика ESC/outside-click.

Даже если legacy формально выключен по флагу, его присутствие в runtime-ветках:
- создаёт **двойные источники правды** (например, boolean-флаги “open”, параллельные WM state),
- требует костылей синхронизации (suppress-guards),
- осложняет тесты и поддержку,
- отвлекает от основной разработки.

В этой спецификации legacy **полностью исключается из функционала**. Референс по верстке сохраняется как:
- исходники компонентов (SFC) в репозитории,
- и “замороженный” текстовый референс в виде снапшотов HTML (Vitest), чтобы верстку можно было сравнивать диффом.

---

## 1) Non-goals (что НЕ делаем здесь)

- Не улучшаем UX/политику ESC/outside-click сверх минимально необходимого для удаления legacy.
- Не внедряем новый UI/страницы/режимы.
- Не делаем “живой showcase” legacy компонентов.

Эта спецификация — про **удаление legacy runtime** и уменьшение сложности.

---

## 2) Стратегия поставки: 3 этапа (PR2 рекомендуется разделить на 2 PR)

### PR1 — WM-only runtime (opt-out удалён; legacy перестаёт исполняться)

**Цель PR1**: убрать legacy ветки исполнения и opt-out: template/ESC routing/`?wm=0`. WM всегда активен.

**Scope PR1**
- Только `simulator-ui/v2`.
- Wiring + удаление/обнуление WM feature-flag и URL helpers, которые обслуживали `wm=0`.
- Legacy файлы (overlay positioning и т.п.) физически не удаляем — это PR3.

**Изменения PR1 (детально)**
1) **Удалить `?wm=0/1` как функциональный переключатель**
   - В `SimulatorAppRoot.vue` убрать чтение query-параметра `wm`.
   - Удалить/обнулить любые runtime-флаги WM, обслуживавшие dual-path (в актуальном коде флаг уже отсутствует).

2) **Удалить WM feature-flag API, завязанный на `wm` query (если ещё есть)**
  - Принцип: после WM-only перехода в runtime не должно быть кода, который:
    - парсит `wm=0/1` из URL;
    - пробрасывает “enabled/disabled” WM в зависимости от URL;
    - держит dual-path в template.
  - В текущем состоянии репозитория отдельного `featureFlag.ts`/`useWindowManagerEnabled()` уже нет — этот пункт остаётся как проверка на регрессии.

3) **Убрать URL helper, сохраняющий `wm=0` при reload**
  - Принцип: в коде не должно быть helper-ов, которые сохраняют/подмешивают `wm=0/1` в URL.
  - В текущем состоянии репозитория отдельного `navigationUrl.ts`/`buildReloadUrlPreservingWmOptOut()` уже нет — этот пункт остаётся как проверка на регрессии.

4) **Удалить legacy ветку template**
   - Удалить/вырезать из runtime шаблона ветку с legacy `<Transition name="panel-slide"> ...`.
   - Оставить только WM `<TransitionGroup name="ws">`.
  - Явно удалить legacy-инспекторы, которые рендерятся вне legacy-панельной транзиции (должны остаться только в WM-слое):
    - `<EdgeDetailPopup v-if="... && !__USE_WINDOW_MANAGER" />`
    - `<NodeCardOverlay v-if="... && !__USE_WINDOW_MANAGER" />`

5) **ESC routing: только WM**
   - В `SimulatorAppRoot.vue` оставить обработчик ESC → `wm.handleEsc()`.
   - Сохранить поддержку cancelable `geo:interact-esc` (nested consumption).
   - Удалить вызовы legacy overlay stack (например, `handleEscOverlayStack`).

6) **Миграция тестов, которые явно использовали `wm=0`**
  - Принцип: после PR1 `wm=0` больше не существует как runtime-путь, поэтому тесты должны проверять WM поведение без opt-out.
  - Обновить `setUrl('/?...&wm=0')` → `setUrl('/?...')` в тестах:
    - `src/components/SimulatorAppRoot.interact.test.ts`
    - `src/components/ManualPaymentPanel.test.ts`
  - Если встречается `wm=0` вместе с `ui=demo`/`devtools=1` — удалить только `wm=0`, остальное оставить.

**AC PR1**
- `?wm=0` больше не влияет на поведение (WM всегда).
- Legacy ветка DOM не появляется в runtime.
- ESC закрывает/step-back окна через WM, без legacy стека.
- В unit-тестах отсутствует зависимость от `wm=0`.

**Проверки PR1**
- `npm --prefix simulator-ui/v2 run typecheck`
- `npm --prefix simulator-ui/v2 run test:unit`
- Ручной smoke:
  - открыть NodeCard;
  - открыть Payment/Trustline/Clearing;
  - ESC работает (step-back / close);
  - закрытие по × работает.

**Риск PR1**
- Низкий: изменения локализованы, откат простым revert.

---

### PR2 — Убрать двойной state и костыли (WM — единственный source of truth)

**Цель PR2**: удалить legacy-шадоу-состояния и suppress-guards, которые появились из-за dual-path.

> Рекомендация: сделать PR2 двумя отдельными PR (PR2a и PR2b), потому что это два независимых изменения с разными рисками:
> - PR2a — чистка state/ownership (низкий риск, структурная правка)
> - PR2b — изменение UX-политики outside-click (средний риск, поведенческая правка)

**Scope PR2**
- NodeCard ownership (open/close) в WM.
- Outside-click policy приводится к единому варианту.

#### PR2a (PR2.A) — NodeCard: удалить legacy shadow-state как источник правды в WM runtime

**Проблема**
- Legacy boolean/shadow state и `windowsMap` (WM) одновременно участвуют в решении показывать/закрывать NodeCard.
- Это порождает гонки и suppress-guards.

**Изменения**
- В WM runtime перестать использовать любые legacy boolean/shadow flags для решения “окно должно быть открыто”.
- Открытость NodeCard определяется только наличием окна в WM.
- Удалить suppress-guards, которые нужны только для синхронизации legacy boolean ↔ WM.

**Затронутые файлы (ожидаемо)**
_Примечание:_ в текущем WM-only runtime отдельный composable с boolean "open" состоянием NodeCard отсутствует.

- `src/composables/useSimulatorApp.ts` (wiring selection/card-open + Step 0 контракт)
- `src/components/SimulatorAppRoot.vue` (Step 5: WM open/close для inspector окон)
- `src/composables/useCanvasInteractions.ts` и `src/composables/useAppCanvasInteractionsWiring.ts` (dblclick semantics)

**Проверки на регрессии (WM-only)**
- Нет логики, которая пытается закрывать/открывать NodeCard через legacy boolean/shadow state.
- Двойной источник правды для "open" отсутствует.

**Нюанс про click/dblclick в `useCanvasInteractions.ts` (важно для реализации PR2a)**
- Нормативное поведение (должно сохраниться в WM-only runtime):
  - **single click по узлу**: в picking-фазах Interact выбирает текущий шаг (в т.ч. `TO` в payment), а вне picking — только selection (не открывает карту).
  - **double click по узлу**: выбирает узел и **открывает NodeCard** (через WM), независимо от picking-фазы.
- Следствие для PR2a: dblclick-обработчик НЕ является “мёртвым алиасом” и не должен быть удалён.

**AC PR2.A**
- Нет логических веток, где NodeCard закрывается/открывается через shadow state.
- Нет suppress-guards для NodeCard pipeline.

#### PR2b (PR2.B) — Outside-click (canvas empty click): следовать Step 0 контракту

**Нормативная политика (фиксируем как правило)**
- Пустой клик по canvas:
  - закрывает **обе** inspector-карточки (edge-detail и node-card),
  - закрывает interact окна (через отмену flow),
  - вызывает `interact.mode.cancel()`.

**Почему**
- Empty click трактуется как “hard dismiss” взаимодействия: пользователь явно кликает в пустоту, чтобы выйти из контекста.
- Это снимает риск путаницы “платёжный flow активен, но пользователь уже переключился/закрыл контекст”.

**Изменения**

- В Step 0 helper `__selectNodeFromCanvasStep0()` (в `useSimulatorApp.ts`) на пустом клике:
  - вызвать `interact.mode.cancel()` (flow → idle),
  - закрыть обе inspector-карточки (edge-detail + node-card) через 2 последовательных UI-close,
  - очистить selection.

- В WM callback `uiCloseTopmostInspectorWindow()` (в `SimulatorAppRoot.vue`) оставить только inspector UI-close:
  - закрывать topmost inspector (edge-detail → node-card),
  - использовать `wmResetEdgeDetailKeepAlive()` чтобы frozen inspector-контекст не переживал outside-click,
  - НЕ закрывать `interact` группу напрямую — interact окно закрывается watcher-ом на `phase='idle'` после `cancel()`.

**Тесты, которые должны защитить контракт Step 0**
- Обязательно учесть `src/composables/useSimulatorApp.windowManagementStep0.test.ts` (оно фиксирует норму: “empty click cancels interact”).
- Добавить/обновить интеграционные unit-тесты в `src/components/SimulatorAppRoot.interact.test.ts`, которые ранее полагались на hard-dismiss.

**AC PR2.B**
- При активном interact flow пустой клик по canvas отменяет flow.
- При открытом node-card пустой клик закрывает node-card.
- При открытом edge-detail пустой клик закрывает edge-detail.
 - При одновременном наличии edge-detail + node-card пустой клик закрывает оба (в любом порядке).

**Проверки PR2 (PR2a и PR2b отдельно)**
- `npm --prefix simulator-ui/v2 run typecheck`
- `npm --prefix simulator-ui/v2 run test:unit`
- Ручные сценарии:
  - payment заполнен → пустой клик → payment сбрасывается (flow cancelled);
  - node-card открыт → пустой клик → закрывается;
  - edge-detail открыт → пустой клик → закрывается.

**Риск PR2**
- Средний: меняется политика outside-click. Риск снижается фиксацией AC и тестами.

---

### PR3 — Полное удаление legacy из runtime + референс верстки (код + снапшоты HTML)

**Цель PR3**
- Удалить legacy runtime код из сборки и из исходников.
- Сохранить референс верстки как: (1) код компонентов и (2) текстовые снапшоты HTML + короткое описание.

#### PR3.B1 — Зафиксировать референс верстки (HTML snapshots)

**Добавить каталог**
- `docs/ru/simulator/frontend/docs/specs/legacy-windows-reference/`

**Добавить тест, который рендерит компоненты и пишет снапшоты HTML (минимальный набор)**
- `simulator-ui/v2/src/legacyReference/legacyWindowsMarkupSnapshots.test.ts`
  - рендерит и снапшотит HTML для:
    - `NodeCardOverlay` (node-card)
    - `EdgeDetailPopup` (edge-detail)
    - `ManualPaymentPanel` (interact-payment)
    - `TrustlineManagementPanel` (interact-trustline)
    - `ClearingPanel` (interact-clearing)

> Примечание: это именно “референс верстки”, не runtime UI. Тест не должен требовать backend.

**Добавить README**
- `docs/ru/simulator/frontend/docs/specs/legacy-windows-reference/README.md`
  - что фиксируем (верстка/DOM), а что не фиксируем (поведение)
  - какие состояния/props используются для рендера в снапшотах
  - ссылки на исходники компонентов
  - дата/версия

#### PR3.B2 — Удалить legacy runtime код из репозитория (не трогая референс верстки)

**Правило**
- Если код больше не исполняется (PR1) и WM — единственный путь (PR2), legacy можно удалить физически.

**Удаляем (кандидаты; перед удалением обязательно проверить usage)**
- Legacy panel positioning:
  - `src/composables/useInteractPanelPosition.ts` (+ `src/composables/useInteractPanelPosition.test.ts`)
- Legacy overlay positioning helpers (удалять только те части, которые больше не используются после PR1/PR2):
  - `src/utils/overlayPosition.ts` (возможна частичная чистка; важно: есть usages вне legacy-панелей, например tooltips)
  - `src/utils/overlayPosition.test.ts`
- Legacy ESC stack:
  - `src/utils/escOverlayStack.ts` (+ `src/utils/escOverlayStack.test.ts`) — если после PR1 нигде не используется

**Отдельно: чистка legacy self-positioning, но без удаления файла**
- `src/composables/useNodeCard.ts` удалять нельзя (в нём есть полезные вычисления для WM, например screen-center/anchor).
- Но после WM-only runtime можно удалить/выпилить legacy-позиционирование и связанные артефакты, которые становятся dead code:
  - `nodeCardStyle`
  - `_reclamp` и `watch(_baseNodeCardStyle)`
  - `getEdgeDirection`
  - `cardRef`
- Цель: оставить только selection + вычисления экранных координат/якорей для WM.

**Важно**
- Удаление делать через поиск usage:
  - сначала убрать импорты,
  - потом удалить файлы.

#### PR3.B3 — Удалить упоминания legacy режимов из документации

- Удалить “как включить legacy/`wm=0`” отовсюду.
- В основной аудит-спеке добавить ссылку на каталог референса верстки (HTML snapshots) как на единственный поддерживаемый референс legacy UI.
 - Уточнение по scope: документы в `docs/ru/simulator/frontend/docs/archive/` и `docs/ru/simulator/frontend/docs/specs/archive/` являются историческими и могут содержать упоминания `wm=0/1`/dual-path. Их не “чистим”, а помечаем как архивные.

**AC PR3**
- В runtime-коде нет `__USE_WINDOW_MANAGER` и нет legacy веток.
- В репозитории нет legacy overlay positioning кода, который раньше требовал поддержки.
- В репозитории есть текстовый референс верстки: снапшоты HTML проходят и дают читаемый diff.

**Решение по `renderMode` в компонентах (обязательно уточнить в PR3)**
- Сейчас ряд компонентов принимает проп `renderMode: 'legacy' | 'wm'` и в legacy-ветке делает self-positioning (absolute left/top).
- В runtime после PR1/PR2 все компоненты должны работать только в WM пути.
- Если HTML snapshots нужны для фиксации внутреннего DOM/классов (а не координат), то в PR3 рекомендуется:
  - выпилить `renderMode` проп и legacy self-positioning из компонентов, оставив только WM-поведение;
  - снапшоты строить на единственном поддерживаемом варианте разметки.
- Альтернатива (если нужно сохранить “историческую” разметку legacy):
  - оставить `renderMode`, но удалить все runtime-wiring/включение legacy режима;
  - снапшоты явно подписать: какие делаются в `wm`, какие в `legacy` (если оба сохраняются).

**Проверки PR3**
- `npm --prefix simulator-ui/v2 run typecheck`
- `npm --prefix simulator-ui/v2 run test:unit`
- (по возможности) `npm --prefix simulator-ui/v2 run build`

**Риск PR3**
- Средний: удаление файлов может вскрыть скрытые импорты. Лечится дисциплиной “сначала убрать usage, потом удалить файл”.

---

## 3) Сквозные критерии успеха (общие)

- Код WM становится проще развивать: одна модель окон, одна политика ESC/outside-click.
- Нет dual-source state (WM единственный source of truth).
- Любые regressions локализуются по PR, а не в огромном коммите.

---

## 4) Чеклисты выполнения

### Чеклист PR1
- [x] `wm` query параметр больше не влияет
- [x] Удалён URL-based WM toggle (нет парсинга `wm=0/1`, нет dual-path в runtime)
- [x] Нет helper-ов, сохраняющих/подмешивающих `wm=0/1` в URL
- [x] Legacy template ветка удалена
- [x] ESC routing только через WM
- [x] Мигрированы unit-тесты, использующие `wm=0`
- [x] Unit tests проходят

### Чеклист PR2
- [x] NodeCard open/close — только через WM
- [x] Удалены suppress-guards (или они стали не нужны)
- [x] Canvas empty click отменяет interact flow
- [x] Unit tests проходят
- [ ] Ручные сценарии пройдены

### Чеклист PR3
- [x] Добавлен референс верстки: `legacyWindowsMarkupSnapshots.test.ts` + README
- [x] Legacy код удалён из репозитория
- [x] Нет битых импортов/ссылок на legacy runtime файлы
- [x] Typecheck/test/build проходят
