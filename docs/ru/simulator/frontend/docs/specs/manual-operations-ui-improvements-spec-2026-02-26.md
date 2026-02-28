# Simulator UI v2 — Спецификация доработок ручных операций

Статус: draft v2 (частично реализовано; Phase 1 выполнена, Phase 2 — реализована (DoD не закрыт: тесты + визуальная проверка), Phase 2.5 — выполнена, Phase 3 — реализована (DoD не закрыт))

## Implementation status (as of 2026-02-27)

Этот раздел фиксирует текущее состояние реализации требований этой спеки в кодовой базе.

### Manual Payment

- DONE: MP-1 To filtering по tri-state targets (unknown/known-empty/known-nonempty) через [`useParticipantsList.ts`](simulator-ui/v2/src/composables/useParticipantsList.ts:1)
- DONE: MP-1b auto-reset выбранного To при изменении known targets в [`ManualPaymentPanel.vue`](simulator-ui/v2/src/components/ManualPaymentPanel.vue:1)
- DONE: MP-2 отображение direct-hop capacity в To options в [`ManualPaymentPanel.vue`](simulator-ui/v2/src/components/ManualPaymentPanel.vue:1)
- DONE: MP-4 inline reason для disabled Confirm + обязательная нормализация amount через [`parseAmountStringOrNull()`](simulator-ui/v2/src/utils/numberFormat.ts:54)
- DONE: MP-6 `(updating…)` индикатор для unknown targets в [`ManualPaymentPanel.vue`](simulator-ui/v2/src/components/ManualPaymentPanel.vue:1)
- DONE: MP-6a prefetch trustlines на старте payment flow в [`startPaymentFlow()`](simulator-ui/v2/src/composables/useInteractMode.ts:1)
- PARTIAL: MP-0 wiring tri-state targets из root отличается от канонического сниппета (см. “Known divergences” ниже): [`SimulatorAppRoot.vue`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:1)
- DONE: MP-3 фильтрация списка From по `available > 0` — реализовано в [`ManualPaymentPanel.vue`](simulator-ui/v2/src/components/ManualPaymentPanel.vue:180)
- DONE: UX-10 disable To-select при known-empty — реализовано в [`ManualPaymentPanel.vue`](simulator-ui/v2/src/components/ManualPaymentPanel.vue:1)

### Manage Trustline

- DONE: TL-1 inline warning при `newLimit < used` + нормализация ввода в [`TrustlineManagementPanel.vue`](simulator-ui/v2/src/components/TrustlineManagementPanel.vue:1)
- DONE: TL-1a create-flow допускает `limit = 0` в [`TrustlineManagementPanel.vue`](simulator-ui/v2/src/components/TrustlineManagementPanel.vue:1)
- DONE: TL-2 debt-guard (учитывает `used` и, при наличии данных, `reverse_used`) в [`TrustlineManagementPanel.vue`](simulator-ui/v2/src/components/TrustlineManagementPanel.vue:1)
- DONE: TL-3 маркировка `(exists)` в create-flow To в [`TrustlineManagementPanel.vue`](simulator-ui/v2/src/components/TrustlineManagementPanel.vue:1)
- DONE: TL-4 prefill newLimit из effectiveLimit в [`TrustlineManagementPanel.vue`](simulator-ui/v2/src/components/TrustlineManagementPanel.vue:1)

### Run Clearing

- DONE: CL-1 loading-state (текст + спиннер) между Confirm и Preview в [`ClearingPanel.vue`](simulator-ui/v2/src/components/ClearingPanel.vue:1)
- DONE: CL-2 статус-индикация заголовка (пункт закрыт уже в тексте спеки)

### EdgeDetailPopup (v2)

- DONE: ED-1 close guard по долгу (использует `used` и `reverse_used`) в [`EdgeDetailPopup.vue`](simulator-ui/v2/src/components/EdgeDetailPopup.vue:1)
- DONE: ED-2 utilization bar (pct + DS tokens) в [`EdgeDetailPopup.vue`](simulator-ui/v2/src/components/EdgeDetailPopup.vue:1)
- DONE: ED-3 quick action Send Payment + wiring в root: [`EdgeDetailPopup.vue`](simulator-ui/v2/src/components/EdgeDetailPopup.vue:1), [`SimulatorAppRoot.vue`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:1)

### NodeCardOverlay (v2)

- DONE: NC-1 edit для IN trustlines в [`NodeCardOverlay.vue`](simulator-ui/v2/src/components/NodeCardOverlay.vue:1)
- DONE: NC-2 available column + формат `avail: …` в [`NodeCardOverlay.vue`](simulator-ui/v2/src/components/NodeCardOverlay.vue:1)
- DONE: NC-3 saturated visual (finite `available <= 0`) в [`NodeCardOverlay.vue`](simulator-ui/v2/src/components/NodeCardOverlay.vue:1)
- DONE: NC-4 quick action Run Clearing + wiring в root: [`NodeCardOverlay.vue`](simulator-ui/v2/src/components/NodeCardOverlay.vue:1), [`SimulatorAppRoot.vue`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:1)

### Feedback & Discoverability (v2)

- DONE: FB-1 Success toast: [`SuccessToast.vue`](simulator-ui/v2/src/components/SuccessToast.vue:1), state `successMessage` в [`useInteractMode.ts`](simulator-ui/v2/src/composables/useInteractMode.ts:1), wiring в [`SimulatorAppRoot.vue`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:1)
- DONE: FB-2 adaptive dismiss для длинных ошибок: [`ErrorToast.vue`](simulator-ui/v2/src/components/ErrorToast.vue:1)
- DONE: FB-3 ESC hint в ActionBar: [`ActionBar.vue`](simulator-ui/v2/src/components/ActionBar.vue:1)

### Remaining TODO (from this spec)

#### Phase 2

- ~~TODO~~ DONE: MP-3 фильтрация From по `available > 0` — реализовано в [`ManualPaymentPanel.vue`](simulator-ui/v2/src/components/ManualPaymentPanel.vue:180)
- ~~TODO~~ DONE: UX-10 disable To-select при known-empty — реализовано в [`ManualPaymentPanel.vue`](simulator-ui/v2/src/components/ManualPaymentPanel.vue:1)
- TODO: Phase 2 DoD — закрыть чекбоксы (тесты + визуальная проверка); см. §14

#### Phase 2.5

- ~~TODO~~ DONE: Включить multi-hop достижимость через backend-first targets (см. [`§7.2`](docs/ru/simulator/frontend/docs/specs/manual-operations-ui-improvements-spec-2026-02-26.md:1208))
- ~~TODO~~ DONE: TTL/refresh-policy для кэша payment-targets (см. [`payment-targets cache`](docs/ru/simulator/frontend/docs/specs/manual-operations-ui-improvements-spec-2026-02-26.md:1253))
- ~~TODO~~ DONE: покрыть AC-MP-15..18 тестами (см. [`AC-MP-15..18`](docs/ru/simulator/frontend/docs/specs/manual-operations-ui-improvements-spec-2026-02-26.md:1280))

#### Consolidated remaining work

Подробный разбор всех оставшихся задач вынесен в §14.

### Known divergences (as implemented)

- Tri-state targets wiring в root реализован не как канонический MP-0 сниппет: вместо прямого `availableTargetIds` от `trustlinesLoading` используется агрегированный “routes loading” и отдельный канал targets; текущая реализация находится в [`SimulatorAppRoot.vue`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:1).
- Backend-first payment targets endpoint используется как основной источник достижимости и сейчас включён в multi-hop режиме (`max_hops = 6`) в [`useInteractMode.ts`](simulator-ui/v2/src/composables/useInteractMode.ts:1).

Дата: 2026-02-26 (v2: 2026-02-26)

Область: Interact UI (real mode), панели ручных операций:
- `ManualPaymentPanel.vue` — отправка ручного платежа
- `TrustlineManagementPanel.vue` — создание / редактирование / закрытие trustline
- `ClearingPanel.vue` — запуск клиринг-цикла
- `EdgeDetailPopup.vue` — быстрая информация / действия по ребру (v2)
- `NodeCardOverlay.vue` — карточка узла с interact-расширениями (v2)
- `ActionBar.vue` — панель быстрых действий (v2)
- `ErrorToast.vue` — уведомление об ошибках (v2)

Вспомогательные модули, затрагиваемые изменениями:
- `useParticipantsList.ts` — сортировка и фильтрация dropdown-списков
- `useInteractMode.ts` — state-machine Interact UI, `availableTargetIds`, `availableCapacity`
- [`simulator-ui/v2/src/composables/interact/useInteractDataCache.ts`](simulator-ui/v2/src/composables/interact/useInteractDataCache.ts:1) — кэш participants/trustlines, `findActiveTrustline()`
- `interact/useInteractFSM.ts` — фазы FSM (`picking-payment-from`, `picking-payment-to`, `confirm-payment`, …)
- `useDestructiveConfirmation.ts` — двухфазное подтверждение (arm → confirm) (v2)
- `interact/useInteractHistory.ts` — лог действий (v2)

## 1. Цель

Привести UX ручных операций к принципу **«UI не предлагает невозможных действий»**:

1. **Фильтрация на входе:** dropdown-списки From/To не содержат заведомо невалидных вариантов.
2. **Прозрачность ограничений:** пользователь видит числовые лимиты/ёмкость **до** подтверждения.
3. **Объяснение блокировки:** каждый disabled-элемент сопровождается понятной причиной.

Ключевая проблема (мотивация):
- На скриншоте в Manual Payment > FROM = Магазин (shop), список To показывает **всех 9 участников**
  включая самого shop и тех, до кого нет активного маршрута.
- Это сбивает пользователя с толку — он тратит время на выбор заведомо невозможного получателя,
  получает backend-ошибку `NO_ROUTE` / `INSUFFICIENT_CAPACITY` пост-фактум.

## 2. Термины и инварианты

| Термин | Определение |
|--------|-------------|
| **trustline direction** | `from → to` = creditor → debtor. `from` устанавливает кредитный лимит на `to`. |
| **payment direction** | Платёж `sender=A → receiver=B` проходит по ребру `B → A` (receiver — это creditor relative to sender). |
| **available capacity** | Для trustline `from → to`: `limit − used`. Каждая единица capacity = возможность провести 1 ед. платежа **от to к from**. |
| **reachable To** | Получатель B достижим из отправителя A, если существует хотя бы один активный trustline `B → A` с `available > 0` (direct hop) **или** существует multi-hop path, по которому можно провести платёж (capacity > 0). |
| **picking phase** | Фаза FSM, в которой пользователь выбирает узел (canvas click или dropdown): `picking-payment-from`, `picking-payment-to`, `picking-trustline-from`, `picking-trustline-to`. |
| **availableTargetIds** | `Set<string> \| undefined` — tri-state список доступных целей (для canvas-подсветки и фильтрации dropdown). Семантика фиксируется как единая для всего документа: `undefined` = **unknown** (trustlines ещё не загружены или идёт обновление); `Set.size > 0` = **known-nonempty**; `Set.size === 0` = **known-empty** (доступных целей нет). **Важно (MUST):** `availableTargetIds` больше не гарантированно `Set` (может быть `undefined`). В коде это выражается как смена типа computed targets с `ComputedRef<Set<string>>` на `ComputedRef<Set<string> | undefined>` (см. MP-1a). Это затрагивает не только панели, но и canvas pipeline: as-is потребители (например, `useSimulatorApp.ts`) ожидают `Set` всегда и делают `.size`/итерации — после смены типа они обязаны обрабатывать `undefined` отдельно. **Важно (as-is баг, MUST-фикс):** текущая реализация имеет fallback «показать всех» при пустом `Set` и не проверяет `available > 0`, что ломает смысл **known-empty**; это считается багом и исправляется требованиями MP-1a + MP-0 wiring tri-state (см. MP-1/MP-6). |
| **available targets tri-state (wiring)** | Parent-компонент обязан **пробрасывать `availableTargetIds = undefined`, пока `trustlinesLoading === true`**, а когда загрузка завершена — пробрасывать реальный `Set` (включая пустой). Это исключает двусмысленность «пусто потому что не загружено» и синхронизирует dropdown и canvas. |

## 3. Scope / Non-goals

### В scope

- Доработки фильтрации и отображения в трёх panel-компонентах.
- Расширение `useParticipantsList.ts`: новый параметр `availableTargetIds` для фильтрации To.
- Расширение `useParticipantsList.ts`: дополнительные данные (capacity) для обогащения option label.
- Новые inline-подсказки/предупреждения внутри существующих `<template>` секций панелей.
- Согласование dropdown-логики с canvas-подсветкой (единый источник списка целей в `useInteractMode.ts`, см. MP-1a).
- **EdgeDetailPopup** (v2): guard кнопки Close при debt, utilization bar, Send Payment shortcut.
- **NodeCardOverlay** (v2): edit для IN trustlines, available column, saturated visual, Run Clearing action.
- **Feedback** (v2): SuccessToast, adaptive dismiss ErrorToast, ESC hint.
- Покрытие изменений unit/component-тестами.

### Вне scope (с обоснованием)

#### 3.1 Новый роутинг страниц
Interact UI работает как overlay поверх canvas-карты (см. `simulator-real-mode-screens-spec.md`, п. 0:
_«мы не вводим отдельный роутинг для экранов — это будут оверлеи/панели поверх карты»_).
Доработки не выходят за пределы трёх существующих panel-компонентов и их composable.
Добавление vue-router или отдельных страниц для ручных операций противоречит принятой архитектуре
«одноэкранной тактической карты» и не нужно для данных улучшений.

#### 3.2 Полный редизайн панелей
Текущие панели (`ManualPaymentPanel`, `TrustlineManagementPanel`, `ClearingPanel`) уже реализуют
полный жизненный цикл операций: picking → confirm → execute → idle.
Они используют дизайн-токены (`ds-panel`, `ds-select`, `ds-btn-*`, `ds-alert-*`), единую систему
позиционирования (см. [`overlayPosition.ts`](simulator-ui/v2/src/utils/overlayPosition.ts:1); в коде может встречаться как `useOverlayPositioning`, но это не отдельный composable-файл) и двухступенчатое подтверждение (`useDestructiveConfirmation`).
Доработки **точечные**: добавить prop, расширить фильтрацию, вставить inline-help.
Полная перерисовка (новый layout, другие компоненты форм, перенос в drawer/modal) —
отдельная инициатива с собственным UX-обзором и выходит за рамки данной задачи.

#### 3.3 Изменение протокола SSE
SSE-события (`tx.updated`, `clearing.done`, `topology.changed`, `run_status`) обрабатываются
в `useSimulatorApp.ts` / `normalizeSimulatorEvent.ts` и влияют на snapshot/graph.
Данная спецификация работает с **уже загруженными данными** (participants, trustlines lists)
и не требует новых типов SSE-событий или изменения формата существующих.
Refresh trustlines-кэша после мутаций уже реализован (`refreshTrustlines({ force: true })`).

#### 3.4 Переписывание бизнес-логики маршрутизации в backend
Backend `PaymentRouter` (`app/core/payments/router.py`) реализует BFS-маршрутизацию с
capacity-aware графом и `has_topology_path()` с hop-limit.
Backend endpoint `action_payment_real` уже возвращает коды `NO_ROUTE` / `INSUFFICIENT_CAPACITY` / `INVALID_AMOUNT`.
Данная спецификация **не меняет** эту логику — фронтенд использует уже доступные данные
(trustlines list с `available`/`used`/`limit`/`status`) для **предиктивной** фильтрации.
Единственное опциональное расширение (Phase 2.5, §7.2) — **новый read-only endpoint**
для получения списка доступных целей, который вызывает существующий `PaymentRouter`
без изменения его алгоритмов.

#### 3.5 v2 Non-goals (Interact panels)

##### 3.5.1 Drag/reposition панелей (v2)
Interact-панели позиционируются через `useInteractPanelPosition.ts` — anchor-based схема
с тремя источниками: edge-click (рядом с ребром), node-card (рядом с нодой), action-bar (CSS default).
Добавление drag-and-drop для панелей — отдельная UX-инициатива:
требуется state для position (persistent vs session), collision avoidance с canvas elements,
touch support. Текущее позиционирование адекватно для compact panels; если панель перекрывает
важный элемент, пользователь может закрыть её (ESC) и начать flow заново.

##### 3.5.2 Keyboard shortcuts для ActionBar (v2)
Добавление горячих клавиш (напр. `Ctrl+P` → Payment, `Ctrl+T` → Trustline) рассмотрено,
но отложено: risk of conflict с browser shortcuts и другими overlay shortcuts.
Global keydown handler (`simulator-ui/v2/src/components/SimulatorAppRoot.vue`, функция `onGlobalKeydown` → событие `geo:interact-esc`) уже обрабатывает только ESC.
Расширение — отдельная итерация после стабилизации основных panel-улучшений.
В рамках данной спецификации ограничиваемся discoverability hint для существующего ESC (FB-3).

##### 3.5.3 History log interactivity (v2)
`InteractHistoryLog.vue` показывает список последних действий (read-only, `pointer-events: none`).
Добавление interactivity (click to repeat, click to view details) требует:
расширения `useInteractHistory.ts` (хранить action parameters, не только text),
нового UI для «detail view», undo/repeat logic.
Это отдельная feature — «action replay» — и выходит за scope «UI не предлагает невозможных действий».

##### 3.5.4 Полная унификация toast-стилей с Design System
В проекте уже есть исключения по toast-стилям (напр. `ErrorToast.vue`: компонент не полностью выровнен с DS).
Полное выравнивание всех toast'ов по DS (палитра, компоненты, токены, темы) — отдельная инициатива и не является обязательным результатом текущего набора UI-улучшений.

## 4. Текущее состояние (что не так)

### 4.A ManualPaymentPanel — текущие проблемы

| # | Проблема | Где в коде | Эффект |
|---|----------|-----------|--------|
| A1 | **FROM dropdown = все участники.** `participantsSorted` — полный отсортированный список без фильтрации. | `simulator-ui/v2/src/composables/useParticipantsList.ts` (`participantsSorted`), `simulator-ui/v2/src/components/ManualPaymentPanel.vue` (вызов `useParticipantsList()` и `<option v-for="p in participantsSorted">`). | Пользователь может выбрать отправителя, у которого нет ни одного исходящего маршрута. |
| A2 | **TO dropdown = все участники кроме FROM.** `toParticipants` фильтрует только `pid !== fromPid`. | `simulator-ui/v2/src/composables/useParticipantsList.ts` (`toParticipants`). | Пользователь видит получателей, до которых заведомо нет маршрута; выбирает → получает ошибку backend. |
| A3 | **Подсветка/доступные цели не согласованы с реальной отправкой.** `availableTargetIds` вычисляется для canvas, но: (1) **не используется** для To dropdown, (2) **не проверяет `available > 0`**, (3) имеет fallback «подсветить всех», даже если маршрутов реально нет. | `simulator-ui/v2/src/composables/useInteractMode.ts` (`availableTargetIds`). | UI предлагает/подсвечивает «доступное», которое на самом деле отправить нельзя. |
| A4 | **Available capacity видна только после выбора обоих.** Показывается на шаге `confirm-payment`, но не в dropdown. | `simulator-ui/v2/src/components/ManualPaymentPanel.vue` (строка Available на confirm). | Пользователь не может сравнить ёмкости до выбора получателя. |
| A5 | **Нет inline-причины disabled Confirm.** Кнопка Confirm disabled через `canConfirm`, но пользователь не видит текстовой причины — только disabled кнопку. | `simulator-ui/v2/src/components/ManualPaymentPanel.vue` (`canConfirm`). | Непонятно, почему нельзя отправить. |
| A6 | **Числовой ввод amount парсится через `Number(amount)` и не нормализуется.** Это создаёт неоднозначности (пробелы, запятая как десятичный разделитель). | `simulator-ui/v2/src/components/ManualPaymentPanel.vue` (`amountNum = Number(amount.value)`). | Пользователь получает «Enter a positive amount» без понятного объяснения формата и/или видит неожиданные ошибки. |
| A7 | **Amount отправляется как raw string (без нормализации).** Даже если UI сделал `Number()` для локальной валидации, в action уходит исходная строка. | `simulator-ui/v2/src/components/ManualPaymentPanel.vue` (callsite отправки amount в confirm/send). | Backend может отклонить ввод из-за пробелов/запятой; возможны расхождения UI-валидации vs backend. |

### 4.B TrustlineManagementPanel — текущие проблемы

| # | Проблема | Где в коде | Эффект |
|---|----------|-----------|--------|
| B1 | **Update disabled при newLimit < used — без сообщения.** As-is `updateValid` включает: `trim()`/non-empty, `Number.isFinite`, порог `> 0`, и проверку `newLimitNum >= usedNum`. UI не объясняет, какое именно условие не выполнено. **Важно:** после продуктового решения TL-1a «limit допускает 0» потребуется пересмотреть валидатор (порог `> 0` → `>= 0`, если 0-limit принят). | `simulator-ui/v2/src/components/TrustlineManagementPanel.vue` (computed `updateValid`) | Кнопка Update серая — пользователь не понимает, что надо ввести >= used (или почему 0 сейчас не принимается). |
| B2 | **Close TL не предупреждает при used > 0.** Backend вернёт `TRUSTLINE_HAS_DEBT` (409), но UI посылает запрос вслепую. | `simulator-ui/v2/src/components/TrustlineManagementPanel.vue` (action `confirmTrustlineClose` wiring + `useDestructiveConfirmation`), backend `app/api/v1/simulator.py` (`action_trustline_close`) | Пользователь получает неожиданную ошибку. |
| B3 | **В create-flow To содержит участников, с которыми уже есть trustline.** `toParticipants` не учитывает существующие trustlines. | `simulator-ui/v2/src/components/TrustlineManagementPanel.vue` (create-flow To dropdown; сейчас использует `useParticipantsList`) | Попытка создать дубликат → backend ошибка. |
| B4 | **newLimit pre-fill использует `props.currentLimit` (snapshot), а не `effectiveLimit` (backend-авторитетный).** Watcher реагирует на фазу, но берёт из props, а не из `effectiveData`. | `simulator-ui/v2/src/components/TrustlineManagementPanel.vue` (watch phase → set `newLimit`; `effectiveLimit` computed) | При stale-snapshot в newLimit pre-fill может быть старое значение. |

### 4.C ClearingPanel — текущие проблемы

| # | Проблема | Где в коде | Эффект |
|---|----------|-----------|--------|
| C1 | **Нет loading-индикатора между Confirm и Preview.** `busy` отключает кнопку, но нет спиннера/текста. | `simulator-ui/v2/src/components/ClearingPanel.vue` (confirm/preview template ветки; `busy`) | Пользователь не видит прогресса, думает UI завис. |
| C2 | **Preview без данных показывает «Preparing preview…» без визуального feedback.** Только текст, нет анимации. | `simulator-ui/v2/src/components/ClearingPanel.vue` (preview ветка показывает только текст) | Неявно — может быть долгая пауза. |

### 4.D EdgeDetailPopup — текущие проблемы (v2)

| # | Проблема | Где в коде | Эффект |
|---|----------|-----------|--------|
| D1 | **Close line кнопка не проверяет used > 0.** `onCloseLine()` вызывает `confirmCloseOrArm(() => emit('closeLine'))` без предварительной проверки `props.used`. Popup отображает Used/Limit/Available — данные есть, но не используются для guard. | `simulator-ui/v2/src/components/EdgeDetailPopup.vue` (handler `onCloseLine`, props `used`) | Аналог TL-2 в другом компоненте: пользователь нажимает Close line → backend возвращает `TRUSTLINE_HAS_DEBT` (409). |
| D2 | **Нет визуальной индикации утилизации trustline.** Popup показывает Used / Limit / Available как plain text. Нет progress bar, percentage, цвета — невозможно мгновенно оценить «насколько загружено ребро». | `simulator-ui/v2/src/components/EdgeDetailPopup.vue` (`.popup__grid`) | Пользователь должен мысленно вычислить used/limit ratio. |
| D3 | **Нет shortcut «Send Payment» из контекста ребра.** Пользователь видит edge A→B, может захотеть отправить платёж между этими узлами. Нет кнопки — нужно закрыть popup, открыть ActionBar, начать payment flow, выбрать From/To вручную. | `simulator-ui/v2/src/components/EdgeDetailPopup.vue` (`.popup__actions`) | Лишние 4 клика для частого действия. |

### 4.E NodeCardOverlay — текущие проблемы (v2)

| # | Проблема | Где в коде | Эффект |
|---|----------|-----------|--------|
| E1 | **IN trustlines не имеют кнопки Edit (✏️).** OUT trustlines (node = debtor, `from_pid = node.id`) имеют `onInteractEditTrustline`, но IN trustlines (node = creditor, `to_pid = node.id`) — только placeholder `<span class="nco-trustline-row__no-edit">`. Кредитор — тот кто **устанавливает лимит** (направление trustline: creditor → debtor). Отсутствие edit для IN — пропуск ключевого use case. | `simulator-ui/v2/src/components/NodeCardOverlay.vue` (IN rows render `nco-trustline-row__no-edit`) | Пользователь не может edit trustline из карточки creditor-ноды. Нужно найти debtor-ноду или использовать ActionBar. |
| E2 | **Нет колонки Available в строках trustlines.** Каждая строка показывает `used / limit`, но `available` — только в hover tooltip (`:title="avail: ${fmtAmt(tl.available)}"`). | `simulator-ui/v2/src/components/NodeCardOverlay.vue` (OUT/IN rows: available только в `title`) | Пользователь должен наводить курсор на каждую строку, чтобы увидеть оставшуюся ёмкость. |
| E3 | **Нет визуальной индикации saturated trustlines.** Trustline с used=500/limit=500 (avail=0) визуально не отличается от used=10/limit=500 (avail=490). Все строки одного стиля. | `simulator-ui/v2/src/components/NodeCardOverlay.vue` (rows без conditional classes) | При исследовании сети пользователь не может instantly видеть «проблемные» (насыщенные) ребра. |
| E4 | **Нет кнопки «Run Clearing» в quick actions.** Quick actions: `💸 Send Payment` и `＋ New Trustline`. Для ноды с множеством saturated trustlines запуск клиринга из контекста — естественное действие. | `simulator-ui/v2/src/components/NodeCardOverlay.vue` (quick actions block) | Лишний шаг: закрыть карточку → ActionBar → Run Clearing. |

### 4.F Feedback & Discoverability — текущие проблемы (v2)

| # | Проблема | Где в коде | Эффект |
|---|----------|-----------|--------|
| F1 | **Нет success toast / positive feedback после операций.** Успешный платёж, создание trustline и т.д. — единственный feedback: строка в `InteractHistoryLog` (bottom-right, pointer-events:none, opacity 0.7-1.0). Нет явного success toast. `ErrorToast` обрабатывает только ошибки. | `simulator-ui/v2/src/components/SimulatorAppRoot.vue` (wiring to ErrorToast + history log), `simulator-ui/v2/src/components/ErrorToast.vue` (только error styling) | Пользователь не получает явного подтверждения успеха деструктивных операций. Может думать «сработало ли?». |
| F2 | **ErrorToast auto-dismiss 4s может быть мало для сложных ошибок.** Сообщения вроде `TRUSTLINE_HAS_DEBT: Cannot close...` или `INSUFFICIENT_CAPACITY: max: 250` содержат важную информацию. 4 секунды — мало на прочтение + осмысление. | `simulator-ui/v2/src/components/ErrorToast.vue` (default `dismissMs: 4000`) | Пользователь может не успеть прочитать ошибку. |
| F3 | **ActionBar hint «Cancel current action first» не указывает способ отмены (ESC).** Hint показывается при активном flow, но пользователь не знает HOW to cancel. ESC обрабатывается глобально и диспатчит событие `geo:interact-esc`, но UI не сообщает об этом. | `simulator-ui/v2/src/components/ActionBar.vue` (hint text), `simulator-ui/v2/src/components/SimulatorAppRoot.vue` (global handler `onGlobalKeydown` → `geo:interact-esc`) | Пользователь может не знать про ESC, ищет кнопку Cancel. |

## 5. Функциональные требования

### 5.1 Manual Payment

#### MP-0 (MUST, Phase 1). Wiring tri-state `availableTargetIds` из `SimulatorAppRoot.vue`

Этот wiring — **обязательное условие** корректной tri-state модели `availableTargetIds` (см. §2) и,
соответственно, корректной реализации MP-1/MP-1a/MP-2/MP-6.

Почему MUST:
- Без явного проброса `availableTargetIds = undefined` на время `trustlinesLoading === true` невозможно различить:
  - **unknown** (ещё грузим/обновляем trustlines)
  - **known-empty** (trustlines известны и direct targets реально нет)
- Без проброса `trustlines` нельзя показывать capacity в option label (MP-2).
- Без проброса `trustlinesLoading` UI не может честно показывать `(updating…)` в unknown.

**Канонический сниппет wiring** (Phase 2.5+; source of truth — реализация в [`SimulatorAppRoot.vue`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:186)):

```vue
<script setup lang="ts">
const trustlinesLoading = computed(() => interact.mode.trustlinesLoading.value)
const paymentTargetsLoading = computed(() => interact.mode.paymentTargetsLoading.value)

// Принятое UX-решение: держим unknown, пока обновляется любой источник маршрутов,
// чтобы не показать stale targets (см. §14.1).
const routesLoading = computed(() => trustlinesLoading.value || paymentTargetsLoading.value)

// MUST: tri-state проброс кодируется строго через `undefined` в unknown.
const availableTargetIds = computed(() =>
  routesLoading.value ? undefined : interact.mode.paymentToTargetIds.value,
)

const trustlines = computed(() => interact.mode.trustlines.value)
</script>

<ManualPaymentPanel
  ...
  :trustlines-loading="routesLoading"
  :available-target-ids="availableTargetIds"
  :trustlines="trustlines"
/>
```

#### MP-1. Фильтрация списка To по доступным целям

**Текущее поведение:**
- `toParticipants` в `simulator-ui/v2/src/composables/useParticipantsList.ts` = все `participantsSorted` минус `fromPid`.
- `availableTargetIds` в `simulator-ui/v2/src/composables/useInteractMode.ts` подсвечивает цели на canvas, но:
  - не проверяет `available > 0` (проверяется только `status`),
  - делает fallback «все участники кроме from», когда `Set` пуст, даже если маршрутов реально нет.

**Требуемое поведение:**
- Tri-state определяется **значением `availableTargetIds`** (единая модель):
  - **unknown**: `availableTargetIds === undefined` → допускается degraded fallback (не фильтруем To по targets)
  - **known-empty**: `availableTargetIds.size === 0` → доступных целей нет
  - **known-nonempty**: `availableTargetIds.size > 0`
- Dropdown To фильтруется в known-* состояниях (empty/nonempty):
  - known-empty → To пуст (кроме `—`) + явный help-текст причины
  - known-nonempty → To = пересечение участников с `availableTargetIds`
- В unknown состоянии dropdown To может показывать fallback «все кроме from», но UI обязан пометить деградацию: `(updating…)` + help-текст, что список может содержать недостижимые цели.
  - **Важно:** в known-empty (`Set.size === 0`) **никакого fallback на «все участники» быть не должно**.

**Конкретные изменения:**

MUST (API change): `useParticipantsList` получает новый публичный параметр `availableTargetIds: MaybeRefOrGetter<Set<string> | undefined>`.
- Требуется обновить **все** вызовы composable в кодовой базе.
- Там, где фильтрация To по targets не нужна — передавать `() => undefined`.

Файл `useParticipantsList.ts`:
```typescript
// PUBLIC API CHANGE (MUST): добавить в UseParticipantsListInput новый параметр.
// Поле НЕ опциональное, но допускает `undefined` как unknown.
// Все вызовы useParticipantsList() в кодовой базе должны быть обновлены.
// В местах, где фильтрация To по targets не нужна, передавать `() => undefined`.
 availableTargetIds: MaybeRefOrGetter<Set<string> | undefined>

// В toParticipants computed:
const targets = toValue(input.availableTargetIds) // Set<string> | undefined
// known-empty: целей точно нет → список To пуст, без fallback.
if (targets !== undefined && targets.size === 0) return []
return participantsSorted.value.filter(p => {
  const pid = (p?.pid ?? '').trim()
  if (pid === from) return false
  // unknown -> fallback (no filter)
  if (targets === undefined) return true
  // known-* -> filter by set (empty already handled above)
  return targets.has(pid)
})
```

Файл `useInteractMode.ts` (MP-1a, см. ниже):
- сделать корректный tri-state источник targets для payment-to.

Файл `ManualPaymentPanel.vue`:
```typescript
// Передать availableTargetIds из нового prop (tri-state):
const { participantsSorted, toParticipants } = useParticipantsList<ParticipantInfo>({
  participants: () => props.participants,
  fromParticipantId: () => props.state.fromPid,
  availableTargetIds: () => props.availableTargetIds,  // новый prop
})
```

 Template `ManualPaymentPanel.vue` (UX):
- Phase 2.5+ (backend-first, §7.2): `availableTargetIds` строится по ответу `payment-targets` и является авторитетным по достижимости (multi-hop).
- если `state.fromPid` выбран, `availableTargetIds` задан и `availableTargetIds.size === 0` (known-empty), показывать help:
  - `Backend reports no payment routes from selected sender.`
- если `availableTargetIds === undefined` (unknown), показывать `(updating…)` рядом с To и help:
  `Routes are updating; the list may include unreachable recipients.`

Файл `SimulatorAppRoot.vue` — см. MP-0 (канонический wiring tri-state).

#### MP-1a. Исправление вычисления доступных целей (canvas + dropdown)

**Проблема:** `availableTargetIds` сейчас:
- включает цели с `available = 0`,
- трактует `ids.size === 0` как «trustlines не загружены» и подсвечивает всех.

**MUST: изменение типа и влияние на потребителей (canvas/highlight):**
- as-is (контракт, который требуется изменить): `availableTargetIds` вычисляется как `ComputedRef<Set<string>>` и всегда возвращает `Set`.
- to-be (контракт Phase 1): `availableTargetIds` вычисляется как `ComputedRef<Set<string> | undefined>`.
  - `undefined` = **unknown**, допускается degraded fallback UI (см. MP-1/MP-6)
  - `Set` (включая пустой) = **known-***
- Это затрагивает всех consumers на canvas/highlight и в dropdown, которые раньше ожидали `Set` всегда:
  - они обязаны обрабатывать `undefined` отдельно
  - fallback «подсветить всех кроме from» разрешён **только** когда `availableTargetIds === undefined`.
  - важно: текущий canvas pipeline (например, в `useSimulatorApp.ts`) as-is делает `.size` и итерации по `availableTargetIds`; после смены типа это должно стать `availableTargetIds?.size`/guard + fallback только для unknown.

**Требуемое поведение:**
- `availableTargetIds` возвращается как tri-state `Set<string> | undefined`:
  - `undefined` в состоянии **unknown** (trustlines ещё не загружены или обновляются). Источник unknown — `trustlinesLoading === true`; parent обязан явно пробрасывать `availableTargetIds = undefined` пока идёт загрузка (см. MP-6 / wiring).
  - `Set` (включая пустой) в known-* состояниях
- Источник истины для `availableTargetIds` зависит от фазы внедрения:
  - Phase 1: direct-hop по trustlines (минимальная реализация ниже)
  - Phase 2.5+: backend-first по endpoint `payment-targets` (см. §7.2)
- Интерпретация tri-state для потребителей:
  - canvas highlight: если `availableTargetIds === undefined` → допускается degraded fallback подсветки (все кроме from), иначе — подсветка только по `Set`
  - dropdown To (MP-1): если `availableTargetIds === undefined` → fallback список + индикатор `(updating…)`, если пустой `Set` → known-empty без fallback
- Для `picking-payment-to` включать цель `tl.from_pid` только если:
  - `tl.to_pid === fromPid`
  - `isActiveStatus(tl.status) === true`
  - `n = parseAmountNumber(tl.available)` и `Number.isFinite(n) && n > 0`
- Fallback «подсветить всех кроме from» разрешён **только** в состоянии unknown (см. tri-state):
  - т.е. когда `availableTargetIds === undefined`.
- Если trustlines известны и релевантные targets отсутствуют → `Set.size === 0` (known-empty) без fallback.

 Минимальная реализация без расширения API (direct-hop heuristic):
 ```ts
 // useInteractMode.ts
 if (phase === 'picking-payment-to' && state.fromPid) {
   // unknown while updating trustlines: consumers use degraded fallback UI
   if (trustlinesLoading.value) return undefined
   const ids = new Set<string>()
   for (const tl of trustlines.value) {
     const avail = parseAmountNumber(tl.available)
     if (tl.to_pid === state.fromPid && isActiveStatus(tl.status) && Number.isFinite(avail) && avail > 0) {
       ids.add(tl.from_pid)
     }
   }
   return ids
 }
 ```

#### MP-1b. Сброс выбранного получателя при обновлении доступных целей

Сценарий: пользователь уже выбрал `toPid`, но trustlines обновились (TTL refresh, мутация в другой вкладке, SSE-triggered refresh), и выбранный `toPid` перестал быть доступным.

**Требуемое поведение:**
- Если `availableTargetIds` в состоянии known-* (не `undefined`) и выбранный `toPid` **не входит** в `availableTargetIds` — UI сбрасывает `toPid` и показывает inline warning:
  `Selected recipient is no longer available. Please re-select.`

Примечание:
- В unknown (`availableTargetIds === undefined`) принудительный сброс не делаем, т.к. фильтрация деградирует.

Примечание: direct-hop heuristic не видит multi-hop; это учтено в §7.2 и рисках.

#### MP-2. Отображение available capacity в каждом пункте To

**Текущее поведение:** `<option>` в To dropdown показывает только `participantLabel(p)` → `Алиса (alice)`.

**Требуемое поведение:**
- Для каждого participant в To-списке вычислить capacity из trustlines list.
- Формат: `Алиса (alice) — 500 UAH`
- Если capacity неизвестна (загрузка): `Алиса (alice) — …`
- Если capacity = 0: участник не должен быть в списке (сработает MP-1).

Примечание (важно для корректных ожиданий Phase 1):
- В Phase 1 label показывает **direct-hop capacity** (по trustline `to → from`).
- Это **не** является backend maximum: при multi-hop backend может разрешить больше или меньше.
- Точность по multi-hop и backend-first source-of-truth обеспечивается в Phase 2.5 (`payment-targets`, см. §7.2).

**Конкретные изменения:**

Добавить в `ManualPaymentPanel.vue` вычисление capacity-map:
```typescript
// Новый prop:
trustlines?: TrustlineInfo[]

// Computed: capacity per To pid
const capacityByToPid = computed<Map<string, string>>(() => {
  const map = new Map<string, string>()
  const from = (props.state.fromPid ?? '').trim()
  if (!from) return map
  const items = Array.isArray(props.trustlines) ? props.trustlines : []
  for (const tl of items) {
    // Payment from -> to uses capacity on trustline to -> from
    if (tl.to_pid === from && isActiveStatus(tl.status)) {
      map.set(tl.from_pid, tl.available ?? '?')
    }
  }
  return map
})

// В option label:
function toOptionLabel(p: ParticipantInfo): string {
  const base = participantLabel(p)
  const pid = (p.pid ?? '').trim()
  const cap = capacityByToPid.value.get(pid)
  if (cap != null) return `${base} — ${cap} ${props.unit}`
  return `${base} — …`
}
```

В template:
```vue
<option v-for="p in toParticipants" :key="p.pid" :value="p.pid">{{ toOptionLabel(p) }}</option>
```

#### MP-3. Фильтрация списка From

**Текущее поведение:** `participantsSorted` = все участники.

**Требуемое поведение (Phase 2):**
- Участник показывается в From, только если для него существует хотя бы один `tl.to_pid === pid` с `available > 0`.
- Это означает: «хотя бы кто-то доверяет этому участнику и у этого доверия есть ёмкость».

**Конкретные изменения:**

Новый computed в `ManualPaymentPanel.vue`:
```typescript
const fromParticipants = computed<ParticipantInfo[]>(() => {
  const items = Array.isArray(props.trustlines) ? props.trustlines : []
  if (items.length === 0) return participantsSorted.value  // fallback
  const pidsWithOutgoing = new Set<string>()
  for (const tl of items) {
    // Важно: сравнения по decimal-like строкам делаем через parseAmountNumber(), а не через Number().
    const avail = parseAmountNumber(tl.available)
    if (isActiveStatus(tl.status) && Number.isFinite(avail) && avail > 0) {
      pidsWithOutgoing.add(tl.to_pid)  // to_pid может отправлять платежи к from_pid
    }
  }
  if (pidsWithOutgoing.size === 0) return participantsSorted.value  // fallback
  return participantsSorted.value.filter(p => pidsWithOutgoing.has((p.pid ?? '').trim()))
})
```

В template FROM select: `v-for="p in fromParticipants"` вместо `v-for="p in participantsSorted"`.

#### MP-4. Inline-причина disabled Confirm

**Текущее поведение:** `canConfirm` = false → кнопка серая, текста нет.

**Требуемое поведение:** под полем Amount показывать конкретную причину:

| Условие | Сообщение |
|---------|-----------|
| `amount` пусто или <= 0 | `Enter a positive amount.` |
| `amount` не соответствует формату (см. UX-8) | `Invalid amount format. Use digits and '.' for decimals.` |
| `exceedsCapacity` = true | **Не блокирует** Confirm. Показывается warning: `Amount may exceed direct trustline capacity (...)... backend will validate.` |
| `canSendPayment` = false при заполненных from/to | `Backend reports no payment routes between selected participants.` |

**Конкретные изменения:**

```typescript
const confirmDisabledReason = computed<string | null>(() => {
  if (props.busy) return null  // don't show text while sending
  const raw = amount.value
  if (!raw.trim()) return 'Enter a positive amount.'
  // `parseAmountStringOrNull()` обязана делать trim() и нормализацию `,`→`.`.
  const normalized = parseAmountStringOrNull(raw)
  if (normalized === null) return "Invalid amount format. Use digits and '.' for decimals."
  // Важно: сравнения по decimal-like строкам делаем через parseAmountNumber(), а не через Number().
  const amountNum = parseAmountNumber(normalized)
  if (!Number.isFinite(amountNum) || amountNum <= 0) return 'Enter a positive amount.'
  // Phase 2.5 multi-hop: exceeding direct capacity should show a non-blocking warning, not disable confirm.
  // Therefore it MUST NOT produce a disabled reason.
  if (exceedsCapacity.value) return null
  if (props.canSendPayment === false) return 'Backend reports no payment routes between selected participants.'
  return null
})
```

Нормализация (обязательное):
- `amountNum`, `amountValid`, `exceedsCapacity` должны вычисляться от `normalized`, а не от raw `amount.value`.
- При отправке: `confirmPayment(normalized)` (где `normalized` получен через `parseAmountStringOrNull`).

MUST (уточнение as-is для корректности): сейчас `ManualPaymentPanel` делает локальную валидацию через `Number()` и отправляет raw string (см. A6/A7). Требование MP-4/UX-8 заменяет это на модель: parse+normalize → вычисления и отправка только normalized.

```vue
<div v-if="isConfirm && confirmDisabledReason" class="ds-help" data-testid="mp-confirm-reason">
  {{ confirmDisabledReason }}
</div>
```

#### MP-5. Согласованность Canvas ↔ Dropdown

**Принцип:** единый источник правды — вычисление целей для payment-to в `useInteractMode.ts` (MP-1a).
- Canvas использует `availableTargetIds` для подсветки.
- Dropdown (через MP-1) использует тот же `Set<string>` для фильтрации.

Критично: и подсветка, и dropdown должны использовать одинаковое правило **`available > 0`** (а не только `status`).

#### MP-6. Loading-индикатор при загрузке trustlines

**Текущее поведение:** пока trustlines загружаются, dropdown показывает fallback (все участники) без индикации.

**Требуемое поведение:**
- Пробросить `trustlinesLoading: boolean` как prop.
- Если `trustlinesLoading = true` — показывать рядом с To label мелкий текст `(updating…)` или spinner.

Дополнение (консистентность с tri-state, обязательно):
- Для всех потребителей dropdown/canvas tri-state кодируется как `availableTargetIds: Set<string> | undefined`.
- **Unknown** обязан выражаться строго как `availableTargetIds === undefined` и напрямую связан с `trustlinesLoading === true`.
- Parent обязан делать:
  - `availableTargetIds = undefined`, пока `trustlinesLoading === true` (unknown)
  - иначе — реальный `Set` (включая пустой, включая known-empty)

Пример wiring — см. MP-0 (канонический wiring tri-state).

#### MP-6a (MUST, Phase 1). `startPaymentFlow()` делает best-effort prefetch trustlines

Спецификация MP-1/MP-1a/MP-2/MP-6 опирается на актуальные trustlines (для вычисления targets и отображения capacity).

**As-is:** `startPaymentFlow()` обновляет только participants (`refreshParticipants()`), из-за чего фильтрация/label-capacity могут опираться на stale snapshot trustlines.

**MUST:** `startPaymentFlow()` должен делать best-effort prefetch trustlines (аналогично trustline-flow):
- инициировать `refreshTrustlines({ force: true })` при старте payment flow
- на время prefetch `trustlinesLoading` должен отражать обновление, чтобы parent (MP-0) пробрасывал unknown (`availableTargetIds = undefined`)
- если refresh trustlines завершился ошибкой и кэш её «проглотил» (см. UX-4 / Silent cache error), UI после завершения `trustlinesLoading` трактует targets как best-effort snapshot

### 5.2 Manage Trustline

#### TL-1. Inline-сообщение при newLimit < used

**Текущее поведение:** `updateValid` вычисляет `newLimitNum >= usedNum`, кнопка Update disabled, но текста нет.

**Требуемое поведение:**

Формат ввода (обязательное, см. UX-8):
- `normalized = parseAmountStringOrNull(newLimit)`
- если `normalized === null` → Update disabled + inline help (текст как в UX-8)
- при отправке `confirmTrustlineUpdate(normalized)` (не raw string)

Валидация значения:
- `newLimit` допускает `0` (см. UX-8)
- `newLimitNum >= usedNum`

```typescript
const updateLimitTooLow = computed(() => {
  if (!newLimit.value.trim()) return false
  return Number.isFinite(newLimitNum.value) && newLimitNum.value < usedNum.value
})
```

```vue
<div v-if="isEdit && updateLimitTooLow" class="ds-alert ds-alert--warn ds-mono" data-testid="tl-limit-too-low">
  New limit must be ≥ used ({{ renderOrDash(effectiveUsed) }} {{ unit }}).
</div>
```

#### TL-1a. Create-flow: `createValid` допускает limit = 0 (>= 0)

Контекст: UX-8 фиксирует продуктовое решение — trustline limit допускает **0** (обнулить лимит без закрытия).

**Требуемое поведение (create-flow):**
- `normalized = parseAmountStringOrNull(limitRaw)`
- `limitNum = parseAmountNumber(normalized)`
- `createValid = normalized !== null && Number.isFinite(limitNum) && limitNum >= 0`

Примечание: это устраняет несостыковку между текущим кодом (исторически `> 0`) и AC-TL-8.
MUST-уточнение as-is: в текущей реализации `createValid` использует строгое `> 0` (см. `TrustlineManagementPanel.vue`, computed `createValid`), и это должно быть изменено на `>= 0`.

#### TL-2. Предупреждение при Close TL с used > 0

**Контекст backend:** Interact UI получает trustlines list через endpoint
`/simulator/runs/{run_id}/actions/trustlines-list` (reference: [`simulator.py`](app/api/v1/simulator.py:1652)).
Close-action в backend отклоняет закрытие с `409 TRUSTLINE_HAS_DEBT`, если `used > 0 || reverse_used > 0`.
`reverse_used` — это долг в обратном направлении.

Ограничение фронтенда (важно): текущий тип `TrustlineInfo` (см. `simulator-ui/v2/src/api/simulatorTypes.ts`) не содержит `reverse_used`, поэтому в Phase 1 UI-guard по долгу может быть только **best-effort** (только по `used`).

**Текущее поведение:** двухфазное подтверждение через `useDestructiveConfirmation` (кнопка `Close TL` → `Confirm close`),
но без проверки used. При `used > 0` backend вернёт ошибку, которую пользователь увидит как красный alert.

**Требуемое поведение:**

Phase 1 (best-effort, без изменения API):
- Если `effectiveUsed > 0`, перед первым нажатием Close TL показать предупреждение:
  `Cannot close: trustline has outstanding debt ({used} {EQ}). Reduce used to 0 first.`
- Кнопка Close TL становится disabled (не просто armed).
- Если `effectiveUsed == 0`, кнопка Close TL может быть enabled, но backend всё ещё может отклонить close из-за `reverse_used`.
  В этом случае UI обязан корректно показать backend-ошибку через ErrorToast (см. AC-TL-9).

Phase 2 (обязательная часть, доведение принципа «UI не предлагает невозможного» до конца):
- Backend **MUST** возвращать `reverse_used` в items ответа `/simulator/runs/{run_id}/actions/trustlines-list`.
  (Backend уже вычисляет `reverse_used` и использует его в close-guard; требуется экспортировать в list.)
- Frontend **MUST** использовать строгий close-guard: **UI блокирует Close при `used > 0 || reverse_used > 0`**.
- Это устраняет UX-кейс: «Close доступен → backend отвечает 409 TRUSTLINE_HAS_DEBT».

Frontend Phase 2 (в тексте спеки, без реализации):
- Обновить типы в [`simulatorTypes.ts`](simulator-ui/v2/src/api/simulatorTypes.ts:1): `TrustlineInfo` / `SimulatorActionTrustlineListItem` включает `reverse_used`.
- `TrustlineManagementPanel` использует строгий guard `used || reverse_used`.

```typescript
// Phase 1: best-effort guard только по used.
// Phase 2: строгий guard по used || reverse_used.
const closeBlocked = computed(() => {
  const u = usedNum.value
  const ru = parseAmountNumber(effectiveReverseUsed.value)
  return (Number.isFinite(u) && u > 0) || (Number.isFinite(ru) && ru > 0)
})
```

```vue
<div v-if="isEdit && closeBlocked" class="ds-alert ds-alert--warn ds-mono" data-testid="tl-close-blocked">
  Cannot close: outstanding debt {{ renderOrDash(effectiveUsed) }} {{ unit }}.
</div>
<button ... :disabled="busy || closeBlocked" @click="onClose">
  {{ closeArmed ? 'Confirm close' : 'Close TL' }}
</button>
```

#### TL-3. Маркировка существующих trustlines в create-flow To

**Текущее поведение:** `toParticipants` в `TrustlineManagementPanel` — все кроме fromPid.

**Требуемое поведение:**
- Для участников, с которыми уже существует активный trustline `from → to`, добавить суффикс `(exists)`.
- Эти пункты не disabled, а визуально отличаются — пользователь может выбрать (FSM перейдёт в `editing-trustline`).

Явное решение (важно для wiring):
- В create-flow To **не используется** `availableTargetIds` и **не выполняется** фильтрация по payment достижимости.
  Trustline можно создать к любому участнику (кроме from), даже если в Phase 1 UI не показывает multi-hop payment targets.

```typescript
const existingToPids = computed<Set<string>>(() => {
  const from = (props.state.fromPid ?? '').trim()
  if (!from) return new Set()
  const items = Array.isArray(props.trustlines) ? props.trustlines : []
  const set = new Set<string>()
  for (const tl of items) {
    if (tl.from_pid === from && isActiveStatus(tl.status)) set.add(tl.to_pid)
  }
  return set
})

function toLabel(p: ParticipantInfo): string {
  const base = participantLabel(p)
  if (existingToPids.value.has((p.pid ?? '').trim())) return `${base} (exists)`
  return base
}
```

#### TL-4. Fix: newLimit pre-fill из effectiveLimit

**Текущее поведение:** watcher `watch(() => props.phase, ...)` берёт `props.currentLimit` (из snapshot через parent).

**Требуемое поведение:** использовать `effectiveLimit`, который уже предпочитает backend-authoritative `selectedTl.limit`.

```typescript
watch(
  () => props.phase,
  (p) => {
    if (p === 'editing-trustline') {
      const cur = effectiveLimit.value  // ← вместо props.currentLimit
      newLimit.value = cur != null && String(cur).trim() ? String(cur) : ''
    }
    ...
  },
)
```

### 5.3 Run Clearing

#### CL-1. Loading-state между Confirm и Preview

**Текущее поведение:** `simulator-ui/v2/src/components/ClearingPanel.vue` — при `isConfirm && busy` кнопка disabled; при `isPreview && !last` → текст «Preparing preview…».
Между ними — мгновенный переход фазы, но backend fetch может быть долгим. Пользователь видит confirm → (пауза, кнопка серая) → preview. Нет визуального indeterminate-спиннера.

**Требуемое поведение:**

```vue
<template v-if="isConfirm">
  <div v-if="busy" class="ds-help cp-loading">
    Running clearing…
  </div>
  <div v-else class="ds-help">This will run a clearing cycle in backend.</div>
  ...
</template>
```

Альтернатива без нового компонента: заменить текст кнопки:
```vue
<button ... :disabled="busyUi" @click="onConfirm">
  {{ busy ? 'Running…' : 'Confirm' }}
</button>
```

#### CL-2. Статус-индикация в заголовке

**Текущее поведение:** заголовки уже различаются (`Run clearing` / `Clearing preview` / `Clearing running`).
→ Достаточно; дополнительная доработка не требуется. Пункт закрыт — уже реализовано.

### 5.4 EdgeDetailPopup (v2)

#### ED-1. Блокировка Close line при used > 0

**Контекст:** Аналог TL-2, но в другом компоненте. `EdgeDetailPopup` показывает Used/Limit/Available как props
и имеет кнопку "Close line" с двухфазным подтверждением (`useDestructiveConfirmation`).

**Текущее поведение (as-is подтверждено):** `onCloseLine()` сразу вызывает `confirmCloseOrArm(() => emit('closeLine'))` без debt-guard (см. `EdgeDetailPopup.vue`, handler `onCloseLine`).
Props `used` передаётся и отображается, но не проверяется перед close action.

**Требуемое поведение:**
- Если `parseAmountNumber(props.used) > 0`, кнопка Close line → disabled + под ней inline hint.
- Применить ту же логику, что TL-2, но адаптированную к compact popup layout.

Phase 1 (best-effort):
- Close line guard учитывает только `used`; при `reverse_used > 0` backend может отклонить close.
Phase 2 (обязательная часть):
- После расширения trustlines-list item полем `reverse_used` (см. TL-2 / §7.1) прокинуть `reverse_used` в popup/edge-flow
  и блокировать close при любом долге: `used > 0 || reverse_used > 0`.

Frontend Phase 2 (в тексте спеки, без реализации):
- `EdgeDetailPopup` переходит на строгий guard `used || reverse_used`.

```typescript
const closeBlocked = computed(() => {
  // Важно: сравнения по decimal-like строкам делаем через parseAmountNumber(), а не через Number().
  const u = parseAmountNumber(props.used)
  const ru = parseAmountNumber(props.reverse_used)
  return (Number.isFinite(u) && u > 0) || (Number.isFinite(ru) && ru > 0)
})
```

```vue
<!-- В popup__actions, перед кнопкой Close line: -->
<div v-if="closeBlocked" class="ds-alert ds-alert--warn ds-mono popup__close-warn" data-testid="edge-close-blocked">
  Debt: {{ renderOrDash(used) }} {{ unit }}
</div>
<button
  class="ds-btn ds-btn--danger ds-btn--sm"
  :disabled="!!busy || closeBlocked"
  @click="onCloseLine"
>
  {{ closeArmed ? 'Confirm close' : 'Close line' }}
</button>
```

#### ED-2. Capacity utilization bar

**Текущее поведение:** Used / Limit / Available — plain text в grid.

**Требуемое поведение:**
- Добавить тонкую полоску (4px height) под grid, показывающую `used / limit` ratio.
- Цвет по утилизации (через DS tokens): success (0-60%), warning (60-85%), danger (85-100%).
- Формат: `XX%` label рядом с bar.

```typescript
const utilizationPct = computed(() => {
  const u = parseAmountNumber(props.used)
  const l = parseAmountNumber(props.limit)
  if (!Number.isFinite(u) || !Number.isFinite(l) || l <= 0) return 0
  return Math.min(100, Math.round((u / l) * 100))
})

const utilizationColor = computed(() => {
  const p = utilizationPct.value
  // IMPORTANT: use design-system tokens only (no new hard-coded colors).
  if (p >= 85) return 'var(--ds-err)'
  if (p >= 60) return 'var(--ds-warn)'
  return 'var(--ds-ok)'
})
```

```vue
<div class="popup__utilization">
  <div class="popup__utilization-bar" :style="{ width: `${utilizationPct}%`, background: utilizationColor }" />
  <span class="popup__utilization-label ds-mono">{{ utilizationPct }}%</span>
</div>
```

#### ED-3. Quick action «Send Payment» из popup

**Текущее поведение:** Actions = Change limit / Close line / Close. Нет Send Payment.

**Требуемое поведение:**
- Добавить кнопку `Send Payment` (secondary, sm) в `.popup__actions`.
- Клик: `emit('sendPayment')`. Parent (`SimulatorAppRoot`) переводит Interact UI из `editing-trustline` в payment flow
  и предзаполняет **оба** конца платежа, чтобы пользователь сразу попадал на `confirm-payment` (останется только ввести amount).

Правило направления:
- trustline `from → to` (creditor → debtor)
- payment, использующий эту ёмкость: `to → from`

```vue
<button
  class="ds-btn ds-btn--secondary ds-btn--sm"
  type="button"
  :disabled="!!busy"
  @click="emit('sendPayment')"
>
  💸 Send Payment
</button>
```

В `SimulatorAppRoot.vue`:
```typescript
function onEdgeDetailSendPayment() {
  const { fromPid, toPid } = interact.mode.state
  if (!fromPid || !toPid) return
  interact.mode.cancel()
  interact.mode.startPaymentFlow()
  // Trustline from→to: payment goes to→from
  interact.mode.setPaymentFromPid(toPid)
  interact.mode.setPaymentToPid(fromPid)
}
```

### 5.5 NodeCardOverlay (v2)

#### NC-1. Edit кнопка для IN trustlines

**Текущее поведение:** OUT trustlines имеют `✏️` кнопку → `onInteractEditTrustline(tl.from_pid, tl.to_pid)`.
IN trustlines имеют пустой placeholder `<span class="nco-trustline-row__no-edit">`.

**Требуемое поведение:**
- IN trustlines также получают кнопку ✏️.
- Клик: `onInteractEditTrustline(tl.from_pid, tl.to_pid)` — идентично OUT (from_pid/to_pid из TrustlineInfo).
- Кнопка логически корректна: `from_pid` для IN trustline — это «другая нода» (creditor),
  которая устанавливает лимит (from → to = creditor → debtor).

```vue
<!-- IN group: заменить placeholder на edit button -->
<button
  class="ds-btn ds-btn--ghost ds-btn--icon nco-trustline-row__edit"
  type="button"
  :disabled="!!interactBusy"
  title="Edit trustline"
  aria-label="Edit trustline"
  @click="onInteractEditTrustline?.(tl.from_pid, tl.to_pid)"
>✏️</button>
```

#### NC-2. Колонка Available в trustline rows

**Текущее поведение:** Строка показывает `used / limit`. Available — только в tooltip.

**Требуемое поведение:**
- Добавить третью колонку `avail:` после `used / limit`.
- Формат: `avail: 150` в `ds-mono` стиле, `font-size: 0.7rem`, `opacity: 0.6`.
- Grid layout: `grid-template-columns: minmax(60px, 80px) 1fr auto auto` (добавить столбец).

```vue
<span class="nco-trustline-row__avail ds-mono">{{ fmtAmt(tl.available) }}</span>
```

```css
.nco-trustline-row {
  grid-template-columns: minmax(60px, 80px) 1fr auto auto;
}

.nco-trustline-row__avail {
  font-size: 0.7rem;
  opacity: 0.6;
  font-variant-numeric: tabular-nums;
  text-align: right;
}
```

Примечание (safety): изменение `grid-template-columns` применяется только к строкам trustlines (`.nco-trustline-row`), поэтому не должно ломать layout других частей `NodeCardOverlay`.

#### NC-3. Визуальная индикация saturated trustlines

**Текущее поведение:** Все строки одинакового стиля.

**Требуемое поведение (упрощено для кросс-браузерности и минимального риска):**
- Если `Number.isFinite(avail)` и `avail <= 0`: добавить class `nco-trustline-row--saturated`.
- Unknown/invalid `available` (неfinite/`NaN`) **не считается saturated** и не должен окрашивать строку.
- Стиль: левый бордер `2px solid var(--ds-err)`.

Примечание:
- Дополнительный warning-уровень (< 15% available) и фоновая заливка через `color-mix()` не требуются в рамках текущей итерации.

```typescript
function tlRowClass(tl: TrustlineInfo): Record<string, boolean> {
  // ВАЖНО (NC-3): saturated только если available — конечное число и <= 0.
  // Unknown/invalid available (NaN) НЕ считается saturated.
  // Поэтому:
  // - используем strict parseAmountNumber()
  // - всегда делаем Number.isFinite() check перед сравнением
  // - parseAmountNumberOrZero() здесь НЕ применять, иначе unknown превратится в 0
  const availRaw = tl.available
  if (availRaw == null || String(availRaw).trim() === '') {
    // Unknown available → не применяем никакой visual class
    return {}
  }
  const avail = parseAmountNumber(availRaw)
  const limit = parseAmountNumber(tl.limit)
  const limitOk = Number.isFinite(limit) && limit > 0
  return {
    'nco-trustline-row--saturated': Number.isFinite(avail) && avail <= 0,
    'nco-trustline-row--warning': Number.isFinite(avail) && avail > 0 && limitOk && avail / limit < 0.15,
  }
}
```

#### NC-4. Quick action «Run Clearing»

**Текущее поведение:** Quick actions: `💸 Send Payment` | `＋ New Trustline`.

**Требуемое поведение:**
- Добавить третью кнопку: `🔄 Run Clearing` (secondary, sm).
- Клик: `onInteractRunClearing?.()` — новый prop callback.
- Parent: `startFlowFromNodeCard({ start: () => interact.mode.startClearingFlow() })`.

UX note:
- Clearing — глобальная операция; при размещении кнопки в контексте ноды UI должен избегать впечатления, что clearing «только для этой ноды».
  Минимально: tooltip/title `Run clearing (global)`.

```vue
<button
  class="ds-btn ds-btn--secondary ds-btn--sm"
  type="button"
  :disabled="!!interactBusy"
  @click="onInteractRunClearing?.()"
>
  🔄 Run Clearing
</button>
```

Props addition:
```typescript
onInteractRunClearing?: () => void
```

### 5.6 Feedback & Discoverability (v2)

#### FB-1. Success toast после операций

**Текущее поведение:** Нет позитивного уведомления. Только `InteractHistoryLog` (non-interactive, opacity-based).

**Требуемое поведение:**
- Создать `SuccessToast.vue` — аналог `ErrorToast.vue`, но со success-стилизацией через DS (без хардкод-цветов).
- Auto-dismiss: базово 2500ms (успех читается быстрее ошибки).
- Для относительно длинных success-сообщений (например, результат клиринга) auto-dismiss увеличивается:
  - если `message.length > 50` → `dismissMs = 3500`
- Trigger: после каждой успешной action в `useInteractMode.ts` (payment → `"Payment sent: {amount} {eq}"`,
  trustline create → `"Trustline created: {from} → {to}"`, trustline update → `"Limit updated: {newLimit} {eq}"`,
  trustline close → `"Trustline closed: {from} → {to}"`, clearing → `"Clearing done: {settled}/{total} cycles"`).

##### State placement (FB-1)

Выбранный вариант (MUST):

- **Variant B:** `successMessage` живёт **вне FSM-state** как отдельный UI-level `ref` в `useInteractMode.ts`.

Почему:
- `InteractState` (в `interact/useInteractFSM.ts`) сейчас не содержит `successMessage`, и расширять FSM-state для toast'ов не требуется.
- SuccessToast — presentation/feedback; он не должен влиять на FSM-переходы и reset FSM.

State:
- `successMessage: Ref<string | null>` в `useInteractMode`.

Минимальная логика таймера (аналогично FB-2, но проще):
```ts
const effectiveDismissMs = computed(() => {
  const len = (props.message ?? '').length
  if (len > 50) return 3500
  return props.dismissMs
})
```

```vue
<!-- В SimulatorAppRoot.vue, рядом с ErrorToast -->
<SuccessToast
  v-if="isInteractUi"
  :message="interact.mode.successMessage"
  @dismiss="interact.mode.successMessage.value = null"
/>
```

Стиль SuccessToast:
- Использовать только существующие дизайн-токены / примитивы DS (без новых хардкод-цветов).
- Вариант A (предпочтительно): стилизация через `ds-alert ds-alert--ok` (см. `simulator-ui/v2/src/dev/DesignSystemDemoApp.vue`) + позиционирование как у `ErrorToast`.
- Вариант B: фон через токен `var(--ds-ok)` / аналогичный токен темы, если он определён в DS.

DS-consistency:
- `ErrorToast.vue` (см. `simulator-ui/v2/src/components/ErrorToast.vue`) уже является частичным исключением по стилизации (не полностью DS).
- В рамках данной спецификации допустимо оставить toast-компоненты исключением по цветам/стилям.
  Полная унификация toast-стилей по DS вынесена в Non-goals (см. §3.5.4).

#### FB-2. Увеличить auto-dismiss для длинных error сообщений

**Текущее поведение:** `ErrorToast`: auto-dismiss = 4000ms для всех ошибок.

**Требуемое поведение:**
- Если `message.length > 80` → `dismissMs = 6000`.
- Если `message.length > 150` → `dismissMs = 8000`.
- Альтернатива: всегда manual dismiss (убрать auto), показать кнопку × (уже есть).

Минимальное изменение в `ErrorToast.vue`:
```typescript
const effectiveDismissMs = computed(() => {
  const len = (props.message ?? '').length
  if (len > 150) return 8000
  if (len > 80) return 6000
  return props.dismissMs
})
```

И использовать `effectiveDismissMs.value` вместо `props.dismissMs` в таймере.

#### FB-3. ESC hint в ActionBar и активных панелях

**Текущее поведение:** ActionBar hint: `"Cancel current action first"`. Нет упоминания ESC.

**Требуемое поведение:**
- ActionBar hint: `"Cancel current action first (ESC)"` или `"Press ESC to cancel current action"`.
- В каждой active панели (ManualPayment, Trustline, Clearing): добавить мелкий hint `(ESC to close)` рядом с заголовком.

ActionBar change:
```vue
<span v-if="isFlowActive" class="action-bar__hint">
  Press ESC to cancel current action
</span>
```

Панели (пример ManualPaymentPanel):
```vue
<div class="ds-panel__title">
  Send Payment
  <span class="ds-help ds-help--subtle">(ESC to close)</span>
</div>
```

## 6. UX требования (детализация)

### UX-1. Предиктивность
Dropdowns фильтруются на клиенте по уже загруженным данным (trustlines list).
Данные обновляются через `refreshTrustlines()` при старте flow и после каждой мутации.
Если данные stale — fallback на полный список с пометкой `(updating…)`.

### UX-2. Прозрачность ограничений
- В каждом dropdown — capacity рядом с именем.
- Под каждым input — inline-причина при ограничении.
- Кнопки disabled + tooltip/text с причиной.
- Utilization bar в EdgeDetailPopup для мгновенной визуальной оценки (ED-2).
- Saturated trustlines визуально выделены в NodeCard (NC-3).

### UX-3. Минимальные изменения интерфейса
- Все изменения внутри существующих `<template>` секций.
- Используются существующие CSS-классы: `ds-help`, `ds-alert ds-alert--warn`, `ds-mono` (класс `ds-help--warn` не существует).
- Не создаются новые оверлеи, модальные окна или страницы.
- Единственный новый компонент: `SuccessToast.vue` (аналог существующего `ErrorToast.vue`).

### UX-4. Graceful degradation
- Если `availableTargetIds === undefined` (**unknown**) — To dropdown может показывать fallback (все минус fromPid), но обязан показывать `(updating…)` + help-текст о деградации.
- Если `availableTargetIds` задан и `availableTargetIds.size === 0` (**known-empty**) — To dropdown не показывает вариантов (кроме `—`) и показывает явную причину (без fallback «все»):
  - `Backend reports no payment routes from selected sender.`
- Важно: текущий trustlines-кэш best-effort и **проглатывает** ошибки загрузки без явного error-state.
  Поэтому UI может гарантированно показывать состояние «updating» только пока `trustlinesLoading === true`.
  После завершения загрузки/обновления, если targets пусты — UI трактует это как `known-empty`.
  Семантика зависит от источника targets:
  - Phase 1 (direct-hop по trustlines): **нет direct targets по текущему snapshot trustlines (best-effort)**.
    Это **не** является гарантией невозможности платежа в backend (multi-hop route может существовать), и не является строгим error-state загрузки.
    Это осознанный продуктовый компромисс до Phase 2.5 (backend-first targets, §7.2).
  - Phase 2.5+ (backend-first по payment-targets): **backend сообщает, что маршрутов нет**.
    Это является авторитетной оценкой достижимости в рамках заданных guardrails endpoint (см. §7.2).
  Если потребуется различать «реально пусто» vs «не удалось загрузить» — это отдельная доработка кэша (out-of-scope для текущей итерации UX).

### UX-5. Contextual actions (v2)
- EdgeDetailPopup и NodeCardOverlay предоставляют context-aware shortcut-кнопки для частых действий.
- Принцип: «действие доступно там, где пользователь уже смотрит на данные».
- NodeCard: Send Payment, New Trustline, **Run Clearing** (NC-4).
- EdgeDetailPopup: Change limit, Close line, **Send Payment** (ED-3).

### UX-6. Positive feedback (v2)
- Каждая успешная мутация сопровождается кратким позитивным toast (SuccessToast, FB-1).
- Toast содержит конкретику: «Payment sent: 100 UAH shop → alice», не просто «Success».
- Auto-dismiss: базово 2500ms; для длинных success-сообщений увеличивается (см. FB-1).

### UX-7. Discoverability (v2)
- ESC как способ отмены явно упоминается в UI (FB-3): hint в ActionBar, подпись в заголовках панелей.
- Keyboard shortcut видим пользователю, а не скрыт.

### UX-8. Формат числового ввода (amount / limit)

Backend требует строгий формат десятичной строки (источник истины: `parse_amount_decimal()` в `app/utils/validation.py`):
- только цифры и опциональная дробная часть через точку: `^\d+(?:\.\d+)?$`
- без пробелов/табов и без экспоненты (`e/E` запрещены)

Важно: backend **не принимает** запятую как десятичный разделитель.

Frontend обязан нормализовать и валидировать ввод через helper `parseAmountStringOrNull()` в `simulator-ui/v2/src/utils/numberFormat.ts`, чтобы:
- **обязательно** делать `trim()` (backend отклоняет leading/trailing whitespace)
- принимать запятую как пользовательский ввод десятичного разделителя и **нормализовать её в точку** перед отправкой (эквивалентно `raw.trim().replaceAll(',', '.')` перед отправкой)
- гарантировать, что в запрос уходит строка, совместимая с `parse_amount_decimal()` (regex: `^\d+(?:\.\d+)?$`)

Продуктовое решение (фиксируем для консистентности с backend):
- payment `amount` должен быть **строго > 0**
- trustline `limit/newLimit` допускает **0** и должен быть **>= 0** (0 = «обнулить лимит», не закрывая ребро)

**Требуемое поведение UI:**
- Перед отправкой: `normalized = parseAmountStringOrNull(raw)`.
  - Если `normalized === null` → Confirm/Update/Create disabled + понятный текст: `Invalid amount format. Use digits and '.' for decimals.`
  - Если `normalized !== null` → в `confirmPayment()` / `confirmTrustlineUpdate()` / `confirmTrustlineCreate()` отправляем **только** `normalized`.
- Нормализация обязательна для обоих кейсов: payment `amount` и trustline `limit/newLimit`.
- Запятая допускается во вводе, но в запрос уходит точка. Пример: raw=`" 1,23 "` → normalized=`"1.23"`.
- При желании повторить backend-лимиты: scale ≤ 18, precision ≤ 50 (иначе показывать `Too many decimal places` / `Number is too long`).

Примечание: это закрывает A6 и убирает класс ошибок, когда UI считает ввод валидным (`Number(' 1 ')`) но backend отклоняет.

#### Контракты helper'ов чисел (обязательное)

As-is (фиксируем, чтобы избежать путаницы при переходе):
- текущий `parseAmountNumber()` фактически ведёт себя как **finite-or-0** (через `asFiniteNumber`): invalid/empty значения превращаются в `0`.

To-be (требования Phase 1, см. CRIT-1 ниже):
- `parseAmountNumber()` становится **strict** (invalid/empty → `NaN`), а для агрегаций вводится отдельный helper `parseAmountNumberOrZero()`.

`parseAmountStringOrNull(v)` MUST:
```ts
export function parseAmountStringOrNull(v: unknown): string | null {
  if (typeof v !== 'string') return null
  const s = String(v).trim().replaceAll(',', '.')
  // MUST: совместимость с backend regex `^\d+(?:\.\d+)?$`
  // (digits only, optional fractional part with dot; no whitespace; no exponent)
  if (!/^\d+(?:\.\d+)?$/.test(s)) return null
  return s
}
```

`parseAmountNumber(v)` MUST быть strict и возвращать `NaN` для invalid/empty значений (включая `''`, `'   '`, `null`, `undefined`, `'NaN'`).
Важно: пустая строка не должна интерпретироваться как `0`.

Нормативное правило использования по всему коду/спеке:
- перед любым сравнением/делением `parseAmountNumber(x)` нужно проверять `Number.isFinite(n)`.

Implementation guardrail (MUST, коротко):
- сравнения/валидации/UI-guards → strict `parseAmountNumber()` + `Number.isFinite()`
- суммы/графики/метрики → `parseAmountNumberOrZero()`

CRIT-1 (MUST): миграция агрегаций, чтобы strict `parseAmountNumber()` не порождал `NaN`-регресс

Если `parseAmountNumber()` становится strict (invalid → `NaN`), то callsite'ы, которые **агрегируют/суммируют** значения,
могут начать накапливать `NaN` (пример: вычисления system balance / агрегированные суммы).

Пример риска (as-is критичный callsite): агрегации в `simulator-ui/v2/src/composables/useSystemBalance.ts` содержат суммирование значений; если хотя бы одно значение станет `NaN`, итоговые вычисления будут `NaN` и UI/графики могут сломаться.

Почему важно зафиксировать as-is: пока `parseAmountNumber()` ведёт себя как finite-or-0, такие агрегации «случайно безопасны» (invalid превращается в 0). После перехода на strict это перестаёт быть верным и требует явной миграции агрегаций на `parseAmountNumberOrZero()`.

Чтобы исключить скрытые регрессии UI/графиков, MUST ввести и применять отдельный helper finite-or-0:

`parseAmountNumberOrZero(v)` MUST:
```ts
export function parseAmountNumberOrZero(v: unknown): number {
  const n = parseAmountNumber(v)
  return Number.isFinite(n) ? n : 0
}
```

Нормативное правило:
- **Сравнения/валидации**: `parseAmountNumber()` + `Number.isFinite()`
- **Агрегации/суммирование/графики**: `parseAmountNumberOrZero()` (или эквивалент finite-or-0)

### UX-9. A11y / i18n (минимум)

- Новые строки UI (help/warn) добавляются на **английском** (как и существующие тексты панелей). i18n механизма в v2 scope не вводим.
- Inline help/validation тексты должны быть привязаны через `aria-describedby` к соответствующим `<input>`/`<select>`:
  - amount input → id `mp-amount-help`
  - To select → id `mp-to-help`
- Toast'ы: `role="alert"` для ошибок (уже есть), `role="status"` или `aria-live="polite"` для success.

### UX-10. Ограничения нативного `<select>` (важно)

- Нативный `<select><option>` ограничен по стилизации и UX:
  - нельзя надёжно показывать «причину disabled» для каждой option (tooltips/rich layout не работают кросс-браузерно)
  - нельзя рассчитывать на кастомную разметку внутри `<option>`
- Поэтому объяснения и причины блокировки делаем **inline под контролом**:
  - общий help-текст под select (например, `Backend reports no payment routes...` или `Routes are updating...`)
  - для отдельных «особых» пунктов разрешены суффиксы в label (напр. `(exists)`, `— {cap} {EQ}`), но без попытки делать сложный UI в option
- Когда список To пуст (known-empty) — показываем только placeholder option `—` и делаем select disabled.

## 7. Требования к данным и API

### 7.1 Используемые данные (без изменения API)

| Источник | Поля | Где используется |
|----------|------|------------------|
| `GET .../participants` → `ParticipantInfo[]` | `pid, name, type, status` | FROM/TO dropdowns |
| `GET /simulator/runs/{run_id}/actions/trustlines-list` → `TrustlineInfo[]` | Phase 1: `from_pid, to_pid, limit, used, available, status`<br>Phase 2 (to-be): + `reverse_used` | Фильтрация To, capacity label, close warning/guard |
| `availableTargetIds` (computed, `useInteractMode.ts`) | `Set<string> \| undefined` | Фильтрация To-dropdown и canvas-подсветка (tri-state: `undefined` = unknown) |
| `availableCapacity` (computed, `useInteractMode.ts`) | `string \| null` | Confirm-шаг: показ лимита |

Все данные уже доступны; новых API-вызовов для Phase 1 не требуется.

Контракт trustlines-list item / `TrustlineInfo` (важно для Phase 2):
- `reverse_used: string` — decimal-like строка (как `used/limit/available`).
- Phase 1: UI работает без `reverse_used` (best-effort), поэтому backend может не отдавать поле; UI обязан корректно обработать отсутствие.
- Phase 2 (обязательная часть): backend **MUST** отдавать `reverse_used` в `/simulator/runs/{run_id}/actions/trustlines-list`.

Минимальный to-be контракт (для Phase 2):
```ts
// Decimal-like строки, совместимые с parse_amount_decimal()
// (формат см. UX-8)
type DecimalString = string

interface TrustlineInfo {
  from_pid: string
  to_pid: string
  limit: DecimalString
  used: DecimalString
  reverse_used: DecimalString
  available: DecimalString
  status: string
}
```

### 7.2 Опциональное расширение API (Phase 2.5, backend-first достижимость)

Цель: сделать достижимость (и, опционально, ёмкость) **backend-first** для payment flow, чтобы:
- UI не предлагал невозможные цели (включая multi-hop),
- не полагаться на direct-hop эвристику Phase 1,
- не превратить UI в «постоянные сетевые запросы».

Фиксируем «источник истины» для `availableTargetIds` в payment flow:
- Phase 1: `availableTargetIds` = direct-hop вычисление по trustlines list (см. MP-1a)
- Phase 2.5+: `availableTargetIds` = `payment-targets.items[].to_pid`
  - используется одинаково и для To-dropdown, и для canvas
  - known-empty в этом режиме трактуется как «backend сообщает, что маршрутов нет» (см. UX-4)

#### Контракт endpoint (MVP + guardrails)

```
GET /api/v1/simulator/runs/{run_id}/payment-targets?equivalent={EQ}&from_pid={PID}

Query params (guardrails):
- max_hops: number (default 6, max 8)
- limit: number (default 200, max 1000)
- include_max_available: boolean (default false)

Response: {
  items: [
    // Каждый item означает, что существует маршрут с capacity > 0 (implicit can_pay=true)
    { to_pid: string, hops: number, max_available?: string },
    ...
  ]
}
```

Guardrails (обязательные оговорки):
- `max_hops` — жёсткое ограничение расчёта (защита от худших случаев).
- `limit` — ограничение количества возвращаемых элементов.
- Backend может применять timeout/time budget на вычисления.

Реализация на backend (в рамках текущего scope): вызвать существующий `PaymentRouter`.
- Phase 2.5 base: вернуть `to_pid + hops` (сам факт наличия item означает `can_pay=true` и `capacity > 0` хотя бы по одному маршруту).
- `max_available` допускается только как **опциональная деградация** через `include_max_available=true`
  (или под фичефлагом), чтобы не делать тяжёлый расчёт «max-flow для каждого to_pid» обязательным.
- Кэш: допускается переиспользовать существующий `_graph_cache` из `PaymentRouter`.

Это убирает неточность: direct-trustline heuristic не видит multi-hop paths (A→B→C, но нет прямого trustline C→A).

#### Дизайн «без постоянных сетевых запросов» (обязательное)

Frontend:
- Запрос `payment-targets` выполняется **один раз при выборе From** (и при смене `equivalent`/`run_id`),
  а не на каждый рендер/ввод суммы.
- Результат кэшируется на фронте (в `useInteractDataCache` или рядом) с TTL/epoch,
  аналогично trustlines cache.

Backend (rationale):
- Допускается кэшировать/переиспользовать граф маршрутизации и инвалидации после мутаций,
  что согласуется с уже существующим `_graph_cache`.

## 8. Acceptance criteria

### AC-MP (Manual Payment)

| ID | Критерий | Способ проверки |
|----|----------|-----------------|
| AC-MP-0 | `SimulatorAppRoot.vue` пробрасывает в `ManualPaymentPanel` три обязательных prop'а для tri-state: `trustlinesLoading`, `availableTargetIds` (строго `undefined` пока `trustlinesLoading=true`, иначе — реальный `Set`, включая пустой), и `trustlines`. | Component: mount root или shallow mount с проверкой передаваемых props. |
| AC-MP-1 | При FROM = shop, список TO не содержит shop. | Unit: `useParticipantsList` с `fromPid='shop'`. |
| AC-MP-2 | При FROM = shop, список TO не содержит участников без trustline `to_pid = shop`. | Unit: подать `availableTargetIds = new Set(['alice','bob'])`, убедиться что в TO только alice и bob. |
| AC-MP-3 | Каждый TO-пункт показывает available capacity. | Component: snapshot содержит `[tl(bob→shop, avail=500)]`, TO-dropdown для from=shop содержит `Боб (bob) — 500 UAH`. |
| AC-MP-4 | Canvas-подсветка совпадает с TO-dropdown списком. | Component: `availableTargetIds` и `toParticipants.map(p=>p.pid)` содержат одинаковые pid. |
| AC-MP-5 | (DEPRECATED) Phase 1 direct-only: при amount > capacity — inline-предупреждение + Confirm disabled. | Исторический критерий; с включённым multi-hop больше не применяется. |
| AC-MP-5b | Phase 2.5+ multi-hop: при amount > direct capacity показывается warning, но Confirm **не disabled**. | Component: ввести 999 при capacity=500, увидеть `mp-confirm-warning`, Confirm enabled. |
| AC-MP-6 | При пустом amount — inline-подсказка «Enter a positive amount.» | Component. |
| AC-MP-7 | Unknown (updating): при `availableTargetIds=undefined` UI показывает fallback To-list (все кроме from) + индикатор `(updating…)`. | Component: `trustlinesLoading=true`, `availableTargetIds=undefined`. |
| AC-MP-8 | Phase 2.5+ (backend-first): при `availableTargetIds=new Set()` (known-empty) → TO dropdown пуст (кроме placeholder) + виден help `Backend reports no payment routes from selected sender.` | Component: `trustlinesLoading=false`, `availableTargetIds=new Set()`. |
| AC-MP-15 | Phase 2.5+ (backend-first): To-dropdown содержит ровно `payment-targets.items[].to_pid` (и только их). | Integration/component: смоделировать включённый backend-first режим и ответ `payment-targets`, сравнить options. |
| AC-MP-16 | Phase 2.5+ (backend-first, multi-hop): `availableTargetIds` для canvas и dropdown совпадает с `payment-targets.items[].to_pid` (источник истины — backend с параметром `max_hops`, default 6, max 8). | Integration/component: сравнить canvas-highlight targets и To options. |
| AC-MP-17 | Phase 2.5+ (backend-first): known-empty показывает текст `Backend reports no payment routes from selected sender` с опциональным суффиксом ` (max hops: N)` и финальной точкой. | Component: `availableTargetIds=new Set()` в backend-first режиме. |
| AC-MP-18 | Phase 2.5+ (backend-first, multi-hop): запрос `payment-targets` (с параметром `max_hops`) выполняется один раз при выборе From и не повторяется при изменении amount/рендере. | Integration/unit: spy на fetch, изменить amount несколько раз, убедиться что fetch не повторился. |
| AC-MP-19 | Confirm step: при `canSendPayment=false` и заполненных from/to показывается inline reason `Backend reports no payment routes between selected participants.` и Confirm disabled. | Component: phase=confirm-payment, canSendPayment=false, amount>0. |
| AC-MP-9 | При amount=`" 10.5 "` в confirm → `confirmPayment()` вызывается с `"10.5"` (нормализация через `parseAmountStringOrNull()`). | Component: spy confirmPayment args. |
| AC-MP-10 | При amount=`"1,23"` → Confirm разрешён и `confirmPayment()` вызывается с `"1.23"` (нормализация запятой). | Component: spy confirmPayment args. |
| AC-MP-11 | MP-3: FROM dropdown показывает только участников, для которых существует хотя бы один активный trustline `tl.to_pid === pid` с `available > 0`. При пустых trustlines (или если не найдено ни одного pid) — fallback на полный список. | Component/unit: trustlines empty → полный список; trustlines non-empty → отфильтровано. |
| AC-MP-12 | Если выбранный `toPid` перестал быть доступным после refresh (pid исчез из `availableTargetIds` при known-*), UI сбрасывает `toPid` и показывает inline warning `Selected recipient is no longer available. Please re-select.` | Component: смоделировать смену prop `availableTargetIds` так, чтобы выбранный pid исчез; assert reset + warning. |
| AC-MP-13 | `startPaymentFlow()` делает best-effort prefetch trustlines: вызывает `refreshTrustlines({ force: true })` (в дополнение к refresh participants) при старте payment flow, чтобы targets/capacity опирались на свежий snapshot. | Unit/integration: spy на `refreshTrustlines` при вызове `startPaymentFlow()`. |
| AC-MP-14 | Known-empty в `useParticipantsList`: при `availableTargetIds = new Set()` `toParticipants` возвращает `[]` (без fallback «все кроме fromPid»). | Unit: `useParticipantsList` с `availableTargetIds=new Set()` и `fromPid` задан. |

### AC-TL (Manage Trustline)

| ID | Критерий | Способ проверки |
|----|----------|-----------------|
| AC-TL-1 | При newLimit (300) < used (500) → inline-сообщение + Update disabled. | Component. |
| AC-TL-2 | При used > 0, Close TL кнопка disabled + inline-причина. | Component: `effectiveUsed = '150'`. |
| AC-TL-3 | В create-flow To-dropdown участники с existing trustline помечены `(exists)`. | Component: trustlines содержит `[{from_pid:'shop', to_pid:'alice'}]`, option для alice содержит `(exists)`. |
| AC-TL-4 | newLimit pre-fill берёт effectiveLimit, а не props.currentLimit. | Unit: snapshot limit=100, backend trustline limit=150, newLimit = '150'. |
| AC-TL-5 | При newLimit=`" 150 "` → в `confirmTrustlineUpdate()` уходит `"150"` (нормализация через `parseAmountStringOrNull()`). | Component: spy confirmTrustlineUpdate args. |
| AC-TL-6 | При newLimit=`"1,23"` → Update разрешён и в `confirmTrustlineUpdate()` уходит `"1.23"` (нормализация запятой). | Component: spy confirmTrustlineUpdate args. |
| AC-TL-7 | При newLimit=`"0"` и used=`"0"` → Update разрешён; в `confirmTrustlineUpdate()` уходит `"0"`. | Component. |
| AC-TL-8 | В create-flow limit=`" 0 "` → Create разрешён; в `confirmTrustlineCreate()` уходит `"0"` (нормализация через `parseAmountStringOrNull()`). | Component: spy confirmTrustlineCreate args. |
| AC-TL-9 | При used=`"0"` кнопка Close TL может быть enabled, но backend всё ещё может вернуть `TRUSTLINE_HAS_DEBT` (reverse debt); в этом случае UI показывает ошибку через ErrorToast. | Component/integration: смоделировать 409-ответ при close, assert ErrorToast. |
| AC-TL-10 | Phase 2: при наличии `reverse_used > 0` Close TL disabled + inline-причина (даже если `used == 0`). | Component: TrustlineInfo содержит `reverse_used='10'`, assert disabled + текст. |

### AC-CL (Clearing)

| ID | Критерий | Способ проверки |
|----|----------|-----------------|
| AC-CL-1 | После Confirm кнопка показывает «Running…» и disabled. | Component: trigger confirm, assert button text. |

### AC-ED (EdgeDetailPopup) (v2)

| ID | Критерий | Способ проверки |
|----|----------|-----------------|
| AC-ED-1 | При used > 0 кнопка Close line disabled + inline-hint «Debt: {used} {EQ}». | Component: mount с `used='150'`, assert disabled + warning text. |
| AC-ED-2 | Utilization bar отображается: 70% → жёлтая полоска, label «70%». | Component: mount с `used='350', limit='500'`, assert bar width и color. |
| AC-ED-3 | Кнопка Send Payment присутствует и emit('sendPayment') работает. | Component: mount, click button, assert emit. |
| AC-ED-4 | При used = 0 кнопка Close line НЕ disabled (guard отключен). | Component: mount с `used='0'`, assert NOT disabled. |
| AC-ED-5 | Phase 2: при `reverse_used > 0` кнопка Close line disabled (даже если `used == 0`). | Component: mount с `used='0'`, `reverse_used='10'`, assert disabled. |

### AC-NC (NodeCardOverlay) (v2)

| ID | Критерий | Способ проверки |
|----|----------|-----------------|
| AC-NC-1 | IN trustlines имеют кнопку ✏️, клик вызывает `onInteractEditTrustline(from, to)`. | Component: mount с IN trustlines, click ✏️, assert callback args. |
| AC-NC-2 | Каждая trustline row показывает `available` значение (не только в tooltip). | Component: mount, assert текст `fmtAmt(available)` в DOM. |
| AC-NC-3 | Saturated trustline (avail=0) имеет class `nco-trustline-row--saturated`. | Component: mount с `used='500', limit='500', available='0'`, assert class. |
| AC-NC-4 | Кнопка «Run Clearing» присутствует и вызывает callback. | Component: mount с `interactMode=true`, click button, assert callback. |

### AC-FB (Feedback & Discoverability) (v2)

| ID | Критерий | Способ проверки |
|----|----------|-----------------|
| AC-FB-1 | После успешного платежа появляется SuccessToast (success styling через DS, напр. `ds-alert--ok`); auto-dismiss по умолчанию 2500ms. | Integration / component: trigger payment action, assert toast visible + timer. |
| AC-FB-2 | Ошибка длиной > 80 символов показывается дольше (6000ms). | Unit: mount ErrorToast с длинным сообщением, проверить таймер. |
| AC-FB-3 | ActionBar hint при активном flow содержит слово «ESC». | Component: mount с phase != idle, assert hint text includes 'ESC'. |
| AC-FB-4 | Длинный success-message (len > 50) auto-dismiss дольше (3500ms). | Unit: mount SuccessToast с message len > 50, проверить таймер. |

### AC-A11Y (UX-9)

| ID | Критерий | Способ проверки |
|----|----------|-----------------|
| AC-A11Y-1 | Amount input в ManualPaymentPanel имеет `aria-describedby="mp-amount-help"`, а help-элемент имеет `id="mp-amount-help"` и содержит актуальный текст валидации. | Component: assert attributes + наличие help node. |
| AC-A11Y-2 | To select в ManualPaymentPanel имеет `aria-describedby="mp-to-help"`, а help-элемент имеет `id="mp-to-help"` и содержит актуальные сообщения (updating / known-empty / reset). | Component. |
| AC-A11Y-3 | SuccessToast имеет `role="status"` или `aria-live="polite"` (ошибки остаются `role="alert"`). | Component: mount toast, assert attrs. |

## 9. Тестирование

### 9.1 Unit-тесты

| Файл | Теcтируемое |
|------|-------------|
| `useParticipantsList.test.ts` (новый/расширить) | Фильтрация `toParticipants` при `availableTargetIds = new Set(...)`: возвращает только пересечение. |
| `useParticipantsList.test.ts` | Fallback unknown: при `availableTargetIds = undefined` — возвращает все кроме fromPid (не фильтрует по targets). |
| `useParticipantsList.test.ts` | Known-empty: при `availableTargetIds = new Set()` — возвращает пустой список (без fallback «все кроме fromPid»). |
| `ManualPaymentPanel.test.ts` (новый) | `confirmDisabledReason` computed: тесты на все 3 ветки. |
| `ManualPaymentPanel.test.ts` | Валидация amount: trim + нормализация запятой в точку перед submit; reject whitespace-only и invalid format. |
| `ManualPaymentPanel.test.ts` | `capacityByToPid` computed: корректная маппинг trustlines → capacity. |
| `ManualPaymentPanel.test.ts` | `toOptionLabel()`: формат `Name (pid) — 500 UAH`. |
| `TrustlineManagementPanel.test.ts` (расширить) | `updateLimitTooLow`: true когда newLimit < used. |
| `TrustlineManagementPanel.test.ts` | `closeBlocked`: true при usedNum > 0. |
| `EdgeDetailPopup.test.ts` (расширить) | `closeBlocked` computed: true при `used > 0`, false при `used = 0`. (v2) |
| `EdgeDetailPopup.test.ts` | `utilizationPct` и `utilizationColor`: корректные значения для 30%, 70%, 95%. (v2) |
| `ErrorToast.test.ts` (новый) | `effectiveDismissMs`: 4000 при len < 80, 6000 при len 80-150, 8000 при len > 150. (v2) |
| `SuccessToast.test.ts` (новый) | `effectiveDismissMs`: 2500 при len ≤ 50, 3500 при len > 50. (v2) |

### 9.2 Component-тесты

| Файл | Сценарий |
|------|----------|
| `ManualPaymentPanel.test.ts` | Mount с participants=[A,B,C,D], trustlines=[B→A,C→A], from=A → TO dropdown = [B,C]. |
| `ManualPaymentPanel.test.ts` | Mount с `availableTargetIds=undefined` и `trustlinesLoading=true` → рядом с TO label текст `(updating…)`, а To-list = fallback (все кроме from). |
| `ManualPaymentPanel.test.ts` | Mount с `trustlinesLoading=false` и `availableTargetIds=new Set()` → TO options пусты + help visible (known-empty, без fallback). |
| `TrustlineManagementPanel.test.ts` | Mount в editing-trustline, newLimit='100', used='200' → alert visible, Update disabled. |
| `TrustlineManagementPanel.test.ts` | Mount в editing-trustline, used='50' → Close TL disabled + warning text. |
| `ClearingPanel.test.ts` | Mount, trigger confirm → button text = `Running…`. |
| `EdgeDetailPopup.test.ts` | Mount с `used='150'` → Close line disabled, debug hint visible. (v2) |
| `EdgeDetailPopup.test.ts` | Mount с `used='0'` → Close line NOT disabled. (v2) |
| `EdgeDetailPopup.test.ts` | Mount → utilization bar visible, Send Payment button present. (v2) |
| `NodeCardOverlay.test.ts` | Mount с IN trustlines → ✏️ button present, click emits correct args. (v2) |
| `NodeCardOverlay.test.ts` | Mount → available column visible in trustline rows. (v2) |
| `NodeCardOverlay.test.ts` | Mount с saturated trustline (avail=0) → row has `--saturated` class. (v2) |
| `NodeCardOverlay.test.ts` | Mount → «Run Clearing» quick action button visible. (v2) |
| `ActionBar.test.ts` (расширить) | Mount с phase != idle → hint includes «ESC». (v2) |
| `SuccessToast.test.ts` (новый) | Mount с message → SuccessToast visible (success styling via DS), auto-dismiss 2500ms (или 3500ms при len > 50). (v2) |

### 9.3 Integration (real mode, e2e)

| Сценарий | Ожидание |
|----------|----------|
| Запуск greenfield-village-100, FROM=shop, dropdown TO | Содержит только участников с trustline `to_pid=shop`. |
| Выбрать FROM=alice → TO, попробовать отправить | Список To отфильтрован; платёж проходит без NO_ROUTE. |
| Trustline panel: newLimit < used → Update | Кнопка disabled, сообщение видимо. |

## 10. План внедрения

### Phase 1 (обязательный минимум)

| Req | Файлы | Оценка |
|-----|-------|--------|
| **UX-8 helpers**: сделать strict `parseAmountNumber()` + добавить `parseAmountNumberOrZero()` + привести **существующую** `parseAmountStringOrNull()` к контракту (и обновить/добавить тесты) | `simulator-ui/v2/src/utils/numberFormat.ts`, `simulator-ui/v2/src/utils/numberFormat.test.ts` | XS |
| **MP-0** (обязательный tri-state wiring из root) | `SimulatorAppRoot.vue` | XS |
| **MP-1** (фильтрация To) | `useParticipantsList.ts`, `ManualPaymentPanel.vue`, `SimulatorAppRoot.vue` | S |
| **MP-1a** (исправление вычисления `availableTargetIds` + tri-state; адаптация canvas pipeline consumers, т.к. теперь `Set \| undefined`) | `useInteractMode.ts`, `SimulatorAppRoot.vue`, `useSimulatorApp.ts` | S |
| **MP-1b** (reset выбранного To при refresh) | `ManualPaymentPanel.vue` | XS |
| **MP-2** (capacity в dropdown) | `ManualPaymentPanel.vue`, `SimulatorAppRoot.vue` | S |
| **MP-4** (причина disabled) | `ManualPaymentPanel.vue` | XS |
| **MP-5** (canvas = dropdown) | Автоматически из MP-1 (единый `availableTargetIds`). | — |
| **MP-6** (loading indicator) | `ManualPaymentPanel.vue`, `SimulatorAppRoot.vue`, `useInteractMode.ts` | XS |
| **MP-6a** (prefetch trustlines в `startPaymentFlow`) | `useInteractMode.ts` | XS |
| **TL-1** (newLimit message) | `TrustlineManagementPanel.vue` | XS |
| **TL-1a** (`createValid >= 0` + убрать любые упоминания/комментарии про запрет 0-limit, если встречаются) | `TrustlineManagementPanel.vue` | XS |
| **TL-4** (fix pre-fill) | `TrustlineManagementPanel.vue` | XS |
| **CL-1** (loading state) | `ClearingPanel.vue` | XS |
| **ED-1** (close blocked при debt) | `EdgeDetailPopup.vue` | XS |
| **FB-3** (ESC hint) | `ActionBar.vue`, `ManualPaymentPanel.vue`, `TrustlineManagementPanel.vue`, `ClearingPanel.vue` | XS |
| **UX-9 (A11y)** (aria-describedby для amount/To help) | `ManualPaymentPanel.vue` | XS |
| Тесты Phase 1 | `useParticipantsList.test.ts`, component tests, `EdgeDetailPopup.test.ts`, `ActionBar.test.ts` | M |

### Phase 2 (рекомендуется)

| Req | Файлы | Оценка |
|-----|-------|--------|
| **MP-3** (фильтрация From) | `ManualPaymentPanel.vue` | S |
| **TL-2** (close guard, строгий учёт долга) | `TrustlineManagementPanel.vue`, [`simulatorTypes.ts`](simulator-ui/v2/src/api/simulatorTypes.ts:1), backend: `/simulator/runs/{run_id}/actions/trustlines-list` экспортирует `reverse_used` | XS |
| **TL-3** (exists marker) | `TrustlineManagementPanel.vue` | XS |
| **NC-1** (edit для IN trustlines) | `NodeCardOverlay.vue` | XS |
| **NC-2** (available column) | `NodeCardOverlay.vue` | XS |
| **NC-3** (saturated visual) | `NodeCardOverlay.vue` | S |
| **NC-4** (Run Clearing action) | `NodeCardOverlay.vue`, `SimulatorAppRoot.vue` | S |
| **ED-2** (utilization bar) | `EdgeDetailPopup.vue` | S |
| **FB-1** (success toast) | `SuccessToast.vue` (новый), `useInteractMode.ts`, `SimulatorAppRoot.vue` | M |
| **FB-2** (adaptive dismiss) | `ErrorToast.vue` | XS |
| Тесты Phase 2 | `NodeCardOverlay.test.ts`, `EdgeDetailPopup.test.ts`, `SuccessToast.test.ts`, `ErrorToast.test.ts` | M |

### Phase 2.5 (payment-targets: backend-first targets)

| Req | Файлы | Оценка |
|-----|-------|--------|
| DONE: **API 7.2** (payment-targets endpoint как источник истины для targets; fetch 1x per From + frontend cache; guardrails) | backend: `app/api/v1/simulator.py`, `PaymentRouter`; frontend: `useInteractMode.ts`, `useInteractDataCache.ts` | M |
| DONE: Тесты Phase 2.5 | Integration/component tests для backend-first targets (AC-MP-15..18) | M |

### Phase 3 (дополнительные улучшения)

| Req | Файлы | Оценка |
|-----|-------|--------|
| **ED-3** (Send Payment из popup) | `EdgeDetailPopup.vue`, `SimulatorAppRoot.vue` | S |
| Тесты Phase 3 | Integration tests | S |

## 11. Риски и ограничения

| Риск | Вероятность | Митигация |
|------|------------|-----------|
| CRIT-1: strict `parseAmountNumber()` (invalid → NaN) может вызвать `NaN` в агрегациях (напр. system balance), что приведёт к регрессу UI/графиков. | Средняя | Ввести и применять helper finite-or-0 для агрегаций: `parseAmountNumberOrZero()` (см. UX-8, контракты helper'ов чисел). |
| Локальная фильтрация To по direct-trustlines пропускает multi-hop получателей. | Средняя | (Исторически) Phase 1: честный direct-only текст для known-empty; Phase 2.5 (текущая модель): backend endpoint `payment-targets` как источник истины по достижимости. |
| Stale trustlines cache (до 15 сек TTL) → dropdown показывает неактуальные capacity. | Низкая (cache invalidated after mutations) | `refreshTrustlines({ force: true })` после каждой мутации уже реализовано. |
| TL-2/ED-1 close guard на фронте — best-effort: backend может учитывать `reverse_used`, который не доступен на фронте (по текущему типу `TrustlineInfo` в `simulator-ui/v2/src/api/simulatorTypes.ts`). | Средняя | Phase 1: UI блокирует close при `used > 0`, при `used == 0` корректно показывает backend-ошибку через ErrorToast (см. AC-TL-9). Phase 2: добавить `reverse_used` в `TrustlineInfo` и сделать guard строгим (см. AC-TL-10). |
| Silent cache error: ошибки загрузки trustlines могут быть «проглочены» кэшем, и UI после завершения загрузки может выглядеть как known-empty (особенно при direct-only фильтрации Phase 1). | Средняя | Known limitation Phase 1: UI показывает честный direct-only текст для known-empty; полноценный error-state для trustlines-cache — отдельная доработка (вне текущей итерации). |
| Perf/DoS риск: расчёт `payment-targets` на больших графах (особенно при включении `max_available`). | Средняя | Guardrails в контракте (`max_hops`, `limit`, timeout/time budget) + кэш (frontend TTL/epoch; backend реюз `_graph_cache`); `max_available` только при `include_max_available=true`. |

## 13. Дополнение требований (2026-02-27)

### Phase 2.5 — Multi-hop targets включены по умолчанию

**Новые требования (уточнение продукта):**
- UI использует backend-first `payment-targets` как источник достижимости с `max_hops = 6` (multi-hop).
- `canSendPayment` в confirm-step больше не должен hard-gate по direct-hop `availableCapacity`.
  - При unknown targets (endpoint не успел/ошибка) допускается degraded режим: allow confirm и полагаться на backend validation.

**Влияние на UX/copy:**
- Все тексты вида `direct trustlines only` для payment-targets должны быть заменены на backend-first формулировки:
  - known-empty From→To list: `Backend reports no payment routes from selected sender.`
  - confirm disabled due to reachability: `Backend reports no payment routes between selected participants.`

### P2.2 — Busy после cancel должен быть объяснён

**Проблема:** после ESC/Cancel UI может быть `busy=true` (операция in-flight), но phase уже `idle` → выглядит как “UI завис”.

**Требование:**
- Ввести флаг `cancelling=true` когда пользователь вызвал cancel во время `busy=true`.
- ActionBar обязан показывать более точный hint/tooltip:
  - hint: `Cancelling… please wait.`
  - title: `Cancelling… please wait for the operation to finish.`
- `cancelling` сбрасывается в `false` при settle исходного промиса (вместе с `busy=false`).
| Performance: вычисление capacity-map для каждого участника в reactive computed. | Низкая (обычно 5-20 участников и 10-50 trustlines) | Computed мемоизирован Vue; пересчёт только при изменении trustlines/fromPid. |
| `isActiveStatus()` filter может не включать все валидные статусы. | Низкая | Следовать текущей реализации `isActiveStatus()` (сейчас: только `'active'`). Если backend/данные добавят новые «активные» статусы — обновить helper и пересмотреть фильтрацию. |
| NC-1 (IN edit): пользователь может не понимать что он edit'ит trustline другого участника (creditor). | Средняя | Tooltip: «Edit trustline (set by {from_name})» — чётко указать кто creditor. |
| FB-1 (SuccessToast): визуальный шум при быстрых последовательных действиях. | Низкая | Короткий auto-dismiss (2500ms) + queue: новый toast заменяет предыдущий. |
| ED-3 (Send Payment из popup): direction confusion — trustline from→to vs payment direction. | Средняя | Кнопка label: «💸 Pay {from_name}» (показать конкретного получателя). |

## 14. Consolidated TODO (as of 2026-02-27)

Этот раздел консолидирует **все** оставшиеся задачи и partial-пункты со всех фаз,
выявленные по результатам code-ревизии фактической реализации.
Каждый пункт привязан к AC/требованию спецификации и содержит: описание, файлы, приоритет, оценку.

---

### 14.1 DONE: MP-0 — Canon wiring divergence (accepted UX decision)

**Статус:** DONE (спека приведена к фактической реализации; strict wiring — осознанное UX-решение)

**Контекст (историческое расхождение):**
Ранее спека §5.1 MP-0 фиксировала упрощённый canonical wiring:
```
availableTargetIds = trustlinesLoading ? undefined : interact.mode.availableTargetIds
```
Реализация в [`SimulatorAppRoot.vue`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:186) использует:
```
routesLoading = trustlinesLoading || paymentTargetsLoading
paymentToTargetIds = routesLoading ? undefined : interact.mode.paymentToTargetIds
```

Это **строже**, чем исходный canonical: known-state задерживается до загрузки обоих источников.
Решение принято намеренно (UX trade-off): пользователь видит `(updating…)` чуть дольше, зато UI не показывает stale targets.

**Выполнено:** canonical snippet в §5.1 MP-0 обновлён и теперь отражает строгий wiring через `routesLoading` и `paymentToTargetIds`.

| Параметр | Значение |
|----------|----------|
| Приоритет | Low |
| Оценка | XS (doc-only) |
| Файлы | спека §5.1 MP-0 |
| AC | — |

---

### 14.2 TODO: Недостающие тесты по AC-идентификаторам

По результатам ревизии, следующие acceptance criteria **фактически покрыты** по логике,
но не привязаны к AC-идентификаторам в именах тестов. Требуется добавить явные тест-кейсы
(или переименовать существующие) для трассируемости.

| # | AC | Компонент | Что нужно | Файл теста | Оценка |
|---|-----|-----------|-----------|------------|--------|
| T-1 | **AC-ED-5** | EdgeDetailPopup | Тест: `reverse_used > 0, used = 0` → Close line disabled. Логика покрыта в `ED-1 (Phase 2)`, но нет alias к AC-ED-5. | [`EdgeDetailPopup.test.ts`](simulator-ui/v2/src/components/EdgeDetailPopup.test.ts) | XS |
| T-2 | **AC-MP-11** | ManualPaymentPanel | Тест: FROM filtered when trustlines have outgoing; fallback на полный список при пустых trustlines. Частично покрыт `MP-3` тестом, но нет привязки к AC-MP-11. | [`ManualPaymentPanel.test.ts`](simulator-ui/v2/src/components/ManualPaymentPanel.test.ts) | XS |
| T-3 | **AC-MP-12** | ManualPaymentPanel | Тест: при refresh `toPid` исчезает из `availableTargetIds` → reset + inline warning `"Selected recipient is no longer available. Please re-select."`. Покрыт MP-1b, но нет явного AC-MP-12. | [`ManualPaymentPanel.test.ts`](simulator-ui/v2/src/components/ManualPaymentPanel.test.ts) | XS |
| T-4 | **AC-TL-10** | TrustlineManagementPanel | Тест: `reverse_used > 0, used = 0` → Close TL disabled + inline warning. Покрыт `TL-2 (Phase 2)`, но нет alias к AC-TL-10. | [`TrustlineManagementPanel.test.ts`](simulator-ui/v2/src/components/TrustlineManagementPanel.test.ts) | XS |
| T-5 | **AC-A11Y-1** | ManualPaymentPanel | Тест: amount input `aria-describedby="mp-amount-help"` + help-элемент с `id="mp-amount-help"`. | [`ManualPaymentPanel.test.ts`](simulator-ui/v2/src/components/ManualPaymentPanel.test.ts) | XS |
| T-6 | **AC-A11Y-2** | ManualPaymentPanel | Тест: To select `aria-describedby="mp-to-help"` + help-элемент с `id="mp-to-help"`. | [`ManualPaymentPanel.test.ts`](simulator-ui/v2/src/components/ManualPaymentPanel.test.ts) | XS |

**Общая оценка:** XS–S (6 тест-кейсов, каждый — 5-15 строк; можно сгруппировать в один PR).

---

### 14.3 TODO: Визуальная проверка (Phase 1 + Phase 2 DoD)

Не пройдены визуальные проверки из DoD §12:

| Phase | Чекбокс | Что проверить |
|-------|---------|---------------|
| Phase 1 | `[ ] Визуальная проверка: full stack + greenfield-village-100 — ручной платёж, To отфильтрован, capacity видна` | Запустить `run_full_stack.ps1 -Action start -ResetDb -FixturesCommunity greenfield-village-100`, открыть Simulator UI, выполнить Manual Payment. |
| Phase 2 | `[ ] Визуальная проверка: NodeCard с IN trustlines показывает ✏️, saturated rows окрашены, success toast появляется` | В том же окружении: кликнуть на ноду, проверить IN trustlines с edit-кнопкой; найти saturated edge (avail=0) → проверить красный бордер; выполнить операцию → проверить SuccessToast. |

**Оценка:** XS (ручная проверка, ~10 мин).

---

### 14.4 TODO: Integration / E2E тесты (§9.3)

В §9.3 спеки описаны integration-сценарии, которые не автоматизированы:

| # | Сценарий | Ожидаемый результат | Оценка |
|---|----------|---------------------|--------|
| E-1 | Greenfield-village-100, FROM=shop, dropdown TO | Содержит только участников с trustline `to_pid=shop`. | M |
| E-2 | FROM=alice → TO → отправить | Список To отфильтрован; платёж проходит без NO_ROUTE. | M |
| E-3 | Trustline panel: newLimit < used → Update | Кнопка disabled, сообщение видимо. | S |
| E-4 | Send Payment из EdgeDetailPopup | Кнопка → pre-fill From/To, confirm step. | S |
| E-5 | TL close с `reverse_used > 0` → 409 → ErrorToast | Backend отклоняет, UI показывает ошибку. | S |

Инструмент: Playwright (инфраструктура в `admin-ui/e2e/`, но для simulator-ui пока нет).

**Приоритет:** Medium (покрытие гарантирует отсутствие регрессий при будущих изменениях).
**Оценка:** M–L (создание playwright-инфраструктуры для simulator-ui + 5 тестов).

---

### 14.5 TODO: `toSelectionInvalidWarning` не сбрасывается при canvas-driven From change

**Проблема:** inline warning `"Selected recipient is no longer available. Please re-select."`
сбрасывается в `onFromChange()` и `onToChange()`, но **не** при canvas-click смене From,
которая вызывает `setFromPid` напрямую, минуя UI-хендлеры.

**Файл:** [`ManualPaymentPanel.vue`](simulator-ui/v2/src/components/ManualPaymentPanel.vue:297)

**Исправление:**
```typescript
// Добавить watcher:
watch(() => props.state.fromPid, () => {
  toSelectionInvalidWarning.value = null
})
```

| Параметр | Значение |
|----------|----------|
| Приоритет | Medium |
| Оценка | XS (2 строки) |
| AC | AC-MP-12 (косвенно) |

---

### 14.6 TODO: ED-3 — contextual button label

**Текущее:** кнопка Send Payment в EdgeDetailPopup использует generic label `💸 Send Payment`.

**Спека (§11, Risk ED-3):** рекомендует `💸 Pay {from_name}` для устранения direction confusion
(trustline `from→to` vs payment `to→from`).

**Файл:** [`EdgeDetailPopup.vue`](simulator-ui/v2/src/components/EdgeDetailPopup.vue:230)

**Исправление:**
```vue
<button ... @click="emit('sendPayment')">
  💸 Pay {{ state.fromPid ?? 'sender' }}
</button>
```
Или с `from_name` (если доступно через props).

| Параметр | Значение |
|----------|----------|
| Приоритет | Low |
| Оценка | XS |
| AC | AC-ED-3 (полировка) |

---

### 14.7 TODO: `reverse_used` в snapshot fallback

**Проблема:** в [`useInteractDataCache.ts`](simulator-ui/v2/src/composables/interact/useInteractDataCache.ts)
маппинг snapshot→trustlines **не включает** `reverse_used`.
Поле доступно **только** при API-fetch. В degraded-режиме (snapshot fallback до загрузки)
close-guard не учитывает reverse debt → false-negative (Close разрешён, backend вернёт 409).

**Риск:** Low — snapshot fallback кратковременен, и backend catch предотвращает некорректное закрытие.

**Исправление:**
- Если backend snapshot (`links[]`) содержит `reverse_used` → добавить маппинг в `_snapshotToTrustlines()`.
- Если не содержит → оставить как known limitation (backend guard достаточен).

| Параметр | Значение |
|----------|----------|
| Приоритет | Low |
| Оценка | XS–S (зависит от наличия поля в snapshot) |
| AC | AC-TL-10 (косвенно) |

---

### 14.8 PARTIAL: Phase 2 DoD — обновить чекбоксы в §12

Все функциональные требования Phase 2 реализованы (MP-3, TL-2, TL-3, NC-1..4, ED-2, FB-1, FB-2).

Обновлено (doc hygiene): в §12 отмечены выполненными чекбоксы, которые уже закрыты автоматизацией/тестами.
Визуальные проверки (Phase 1/2) намеренно остаются `[ ]` до ручной проверки (§14.3).

| Параметр | Значение |
|----------|----------|
| Приоритет | Low (doc hygiene) |
| Оценка | XS |

---

### 14.9 TODO: Phase 3 DoD — integration-тесты для ED-3

ED-3 (Send Payment из popup) реализован: кнопка + emit + wiring в root с direction reversal.
Не хватает integration-теста (Send Payment → pre-fill → confirm flow end-to-end).

**Файлы:**
- Тест: добавить в [`SimulatorAppRoot.interact.test.ts`](simulator-ui/v2/src/components/SimulatorAppRoot.interact.test.ts)
  или новый `EdgeDetailPopup.integration.test.ts`.
- Проверить: `onEdgeDetailSendPayment()` в [`SimulatorAppRoot.vue`](simulator-ui/v2/src/components/SimulatorAppRoot.vue).

| Параметр | Значение |
|----------|----------|
| Приоритет | Medium |
| Оценка | S |
| AC | AC-ED-3 |

---

### 14.10 Сводная таблица приоритетов

| # | Задача | Приоритет | Оценка | Блокирует DoD |
|---|--------|-----------|--------|---------------|
| 14.5 | `toSelectionInvalidWarning` reset при canvas-click | **Medium** | XS | Нет (edge case) |
| 14.2 | Недостающие тесты по AC (6 шт.) | **Medium** | S | Phase 2 DoD ✅ |
| 14.9 | Integration-тест ED-3 | **Medium** | S | Phase 3 DoD ✅ |
| 14.3 | Визуальная проверка Phase 1 + 2 | **Medium** | XS (manual) | Phase 1+2 DoD ✅ |
| 14.4 | E2E тесты (Playwright) | **Medium** | M–L | Нет (nice-to-have) |
| 14.1 | MP-0 canon divergence (doc) | Low | XS | Нет |
| 14.6 | ED-3 contextual label | Low | XS | Нет |
| 14.7 | `reverse_used` snapshot fallback | Low | XS–S | Нет |
| 14.8 | Phase 2 DoD чекбоксы | Low | XS | Нет (meta) |

## 12. Definition of done

### Phase 1
- [x] Реализованы: MP-0, MP-1, MP-1a, MP-1b, MP-2, MP-4, MP-5, MP-6, MP-6a, TL-1, TL-4, CL-1, ED-1, FB-3, UX-9 (частично: MP aria-describedby).
- [x] Пройдены unit-тесты: `useParticipantsList` фильтрация + fallback, capacity map, disabled reason.
- [x] Пройдены component-тесты: ManualPaymentPanel, TrustlineManagementPanel, ClearingPanel, EdgeDetailPopup, ActionBar.
- [x] `npm run typecheck` проходит без ошибок в `simulator-ui/v2`.
- [x] `npm run test:unit` проходит без ошибок в `simulator-ui/v2`.
- [ ] Визуальная проверка: запустить full stack с greenfield-village-100, выполнить ручной платёж — список To отфильтрован, capacity видна.

Примечание (фактический статус реализации): некоторые пункты из следующих фаз уже реализованы раньше плана (без изменения backend API):
- ED-2 (utilization bar в EdgeDetailPopup)
- ED-3 (Send Payment shortcut в EdgeDetailPopup)
- FB-1 (SuccessToast)

### Phase 2
- [x] Реализованы: MP-3, TL-2, TL-3, NC-1, NC-2, NC-3, NC-4, ED-2, FB-1, FB-2.
- [x] Пройдены component-тесты для NodeCardOverlay, SuccessToast, ErrorToast (adaptive dismiss).
- [x] Дополнить тесты по AC-идентификаторам: AC-ED-5, AC-MP-11, AC-MP-12, AC-TL-10 (см. §14).
- [ ] Визуальная проверка: NodeCard с IN trustlines показывает ✏️, saturated rows окрашены, success toast появляется.

### Phase 2.5
- [x] Frontend: кэш + tri-state wiring для payment-targets (loading/error) и честный degraded UX.
  - [`simulator-ui/v2/src/composables/interact/useInteractDataCache.ts`](simulator-ui/v2/src/composables/interact/useInteractDataCache.ts:1)
  - [`simulator-ui/v2/src/composables/useInteractMode.ts`](simulator-ui/v2/src/composables/useInteractMode.ts:1)
  - [`simulator-ui/v2/src/components/SimulatorAppRoot.vue`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:1)
  - [`simulator-ui/v2/src/components/ManualPaymentPanel.vue`](simulator-ui/v2/src/components/ManualPaymentPanel.vue:1)
- [x] Backend: API 7.2 (payment-targets endpoint) как источник истины по достижимости (multi-hop) + contract/guardrails.
- [x] Пройдены component/integration тесты для backend-first режима (AC-MP-15..18).

### Phase 3
- [x] Реализованы: ED-3.
- [x] Integration-тесты: Send Payment из edge popup.
- [x] UX-полировка: ED-3 button label → contextual `💸 Pay {from_name}` (см. §14).
