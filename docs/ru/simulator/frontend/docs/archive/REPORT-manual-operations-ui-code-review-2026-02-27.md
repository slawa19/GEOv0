# Проверка реализации замечаний: Manual Operations UI Code Review (2026‑02‑27)

Источник замечаний: [`manual-operations-ui-improvements-spec-2026-02-26.md`](../specs/manual-operations-ui-improvements-spec-2026-02-26.md).

---

## Результат проверки по каждому пункту

### P0 — Критические (могут вводить пользователя в заблуждение)

#### P0.1 — Семантический конфликт: multi-hop targets vs direct-hop `canSendPayment`

| Параметр | Значение |
|---|---|
| **Статус** | ✅ Исправлено |
| **Файл** | `useInteractMode.ts` |

**Что сделано:**  
Введена константа `PAYMENT_TARGETS_MAX_HOPS = 1`, и backend-запрос для targets теперь делается строго в direct-only режиме. Это устраняет семантический разрыв: targets и `canSendPayment`/`availableCapacity` теперь используют одну и ту же семантику — прямой хоп.

```ts
// useInteractMode.ts
const PAYMENT_TARGETS_MAX_HOPS = 1
// NOTE: keep payment targets consistent with current UI gating.
// The payment confirmation step uses direct trustline capacity as a hard limit.
// Therefore, for now we query backend targets in direct-only mode.
```

**Замечания к реализации:**  
Выбран правильный вариант из двух предложенных в ревью (direct-only, а не переработка `canSendPayment`). Комментарий явно документирует намеренность решения и содержит указание на точку будущего расширения (`When we introduce multi-hop capacity semantics`). Реализовано корректно.

---

#### P0.2 — Некорректный текст Close-warning при reverse debt (`used=0, reverse_used>0`)

| Параметр | Значение |
|---|---|
| **Статус** | ✅ Исправлено |
| **Файл** | `TrustlineManagementPanel.vue` |

**Что сделано:**  
Computed `closeDebtText` теперь различает три случая: только `used`, только `reverse`, оба долга.

```ts
// TrustlineManagementPanel.vue
const closeDebtText = computed(() => {
  ...
  if (usedDebt && reverseDebt) return `used: ${used} ${unit}, reverse: ${reverse} ${unit}`
  if (usedDebt)    return `used: ${used} ${unit}`
  if (reverseDebt) return `reverse: ${reverse} ${unit}`
  return `used: ${used ?? '—'} ${unit}` // fallback (недостижим при корректном closeBlocked)
})
```

Шаблон:
```html
Cannot close: trustline has outstanding debt ({{ closeDebtText }}). Reduce debt to 0 first.
```

**Замечания к реализации:**  
При `used=0, reverse_used=5` пользователь теперь увидит `reverse: 5 UAH`, что точно объясняет причину блокировки. Реализовано корректно.

Аналогичный паттерн применён и в `EdgeDetailPopup.vue` (см. P1.4).

---

### P1 — Точечные дефекты / качество

#### P1.3 — Баг `'0'` capacity как unknown в `toOptionLabel()`

| Параметр | Значение |
|---|---|
| **Статус** | ✅ Исправлено |
| **Файл** | `ManualPaymentPanel.vue` |

**Что сделано:**  
Проверка изменена с `if (!cap)` (которая трактует `'0'` как falsy) на `if (cap == null)` (только `null`/`undefined`):

```ts
function toOptionLabel(p: ParticipantInfo): string {
  const pid = (p?.pid ?? '').trim()
  const cap = pid ? capacityByToPid.value.get(pid) : undefined
  if (cap == null) return `${participantLabel(p)} — …`  // строка '0' сюда НЕ попадёт
  return `${participantLabel(p)} — ${cap} ${props.unit}`
}
```

**Замечания к реализации:**  
Исправлено корректно. `Map.get()` возвращает `undefined` для отсутствующих ключей, а `'0' == null` — `false`. Строка `'0'` теперь отображается как `Name — 0 UAH`, а не как `Name — …`.

---

#### P1.4 — Debt-hint в EdgeDetailPopup показывает только один из долгов

| Параметр | Значение |
|---|---|
| **Статус** | ✅ Исправлено |
| **Файл** | `EdgeDetailPopup.vue` |

**Что сделано:**  
`closeDebtDisplay` теперь показывает оба долга, когда оба присутствуют:

```ts
const closeDebtDisplay = computed(() => {
  ...
  if (usedDebt && reverseDebt)
    return `used: ${renderOrDash(props.used)} ${props.unit}, reverse: ${renderOrDash(props.reverseUsed)} ${props.unit}`
  if (usedDebt)    return `used: ${renderOrDash(props.used)} ${props.unit}`
  if (reverseDebt) return `reverse: ${renderOrDash(props.reverseUsed)} ${props.unit}`
  return `used: ${renderOrDash(props.used)} ${props.unit}`
})
```

**Замечания к реализации:**  
Реализовано симметрично TrustlineManagementPanel. Корректно.

---

#### P1.5 — A11y: `aria-valuenow=0` при неизвестной утилизации

| Параметр | Значение |
|---|---|
| **Статус** | ✅ Исправлено |
| **Файл** | `EdgeDetailPopup.vue` |

**Что сделано:**  
`:aria-valuenow` теперь явно передаёт `undefined` когда `utilizationPct == null`:

```html
<div
  role="progressbar"
  :aria-valuenow="utilizationPct == null ? undefined : utilizationPct"
  aria-valuemin="0"
  aria-valuemax="100"
>
```

**Замечания к реализации:**  
В Vue 3 привязка к `undefined` полностью убирает атрибут из DOM (атрибут отсутствует в разметке), что правильно сигнализирует скринридерам о неизвестном значении. Реализовано корректно.

---

#### P1.6 — NC-2: видимая колонка Available без префикса `avail:`

| Параметр | Значение |
|---|---|
| **Статус** | ✅ Исправлено |
| **Файл** | `NodeCardOverlay.vue` |

**Что сделано:**  
Колонка теперь отображает `avail:` как видимый текст в `<span>`, а не только в атрибуте `title`:

```html
<!-- OUT row -->
<span class="nco-trustline-row__avail ds-mono">avail: {{ fmtAmt(tl.available) }}</span>

<!-- IN row — аналогично -->
<span class="nco-trustline-row__avail ds-mono">avail: {{ fmtAmt(tl.available) }}</span>
```

**Замечания к реализации:**  
Соответствует тексту спеки. Реализовано корректно.

---

### P2 — Тесты / надёжность / edge cases

#### P2.7 — Toast таймеры не перезапускаются при повторе того же текста

| Параметр | Значение |
|---|---|
| **Статус** | ✅ Реализовано + ✅ Тесты добавлены |
| **Файлы** | `useInteractMode.ts`, `SuccessToast.vue`, `SuccessToast.test.ts`, `ErrorToast.test.ts` |

**Что сделано (реализация):**  
В `useInteractMode.ts` добавлена функция `setSuccessToastMessage()`, которая при повторном одинаковом сообщении сначала сбрасывает в `null`, затем через `scheduleMicrotask` выставляет сообщение снова. Это заставляет watcher `SuccessToast.vue` сработать:

```ts
function setSuccessToastMessage(msg: string) {
  if (successMessage.value === msg) {
    successMessage.value = null
    scheduleMicrotask(() => { successMessage.value = msg })
    return
  }
  successMessage.value = msg
}
```

Аналогичный паттерн реализован для ErrorToast в `runBusy()`:

```ts
if (state.error === msg) {
  state.error = null
  scheduleMicrotask(() => { if (isCurrent()) state.error = msg })
} else {
  state.error = msg
}
```

**Что сделано (тесты):**  
Добавлено покрытие:
1. «same message twice retriggers» — в `useInteractMode.test.ts` (проверяется microtask reset на примере повторного `confirmTrustlineClose()`)
2. «unmount clears timer» — в `SuccessToast.test.ts` и `ErrorToast.test.ts`
3. «manual dismiss prevents auto-dismiss later» — в `SuccessToast.test.ts` и `ErrorToast.test.ts`

---

#### P2.8 — «Тихий busy» после ESC/cancel во время in-flight операции

| Параметр | Значение |
|---|---|
| **Статус** | ✅ Реализовано (UI-объяснение состояния) |
| **Файлы** | `ActionBar.vue`, `ActionBar.test.ts` |

**Что сделано:**  
Семантика `busy` в `useInteractMode.ts` сохранена (busy остаётся `true` до settle in-flight промиса), но UX перестал быть «тихим»:
- в idle-фазе при `busy=true` ActionBar показывает видимую подсказку `Operation in progress… please wait.`
- кнопки получают объясняющие `title` (disabled reason)
- добавлен unit-тест на этот сценарий

---

#### P2.9 — Недостающие тесты: preview-loading (ClearingPanel) и tri-state wiring (root)

| Параметр | Значение |
|---|---|
| **Статус** | ✅ Реализовано |
| **Файлы** | `ClearingPanel.test.ts`, `SimulatorAppRoot.interact.test.ts` |

**ClearingPanel.test.ts:**  
Добавлен тест preview-loading (phase=`clearing-preview`, `lastClearing=null` → spinner/placeholder виден).

**SimulatorAppRoot.interact.test.ts:**  
Добавлен MP-0 тест, который проверяет tri-state wiring: при `routesLoading=true` панель видит unknown-state и показывает help "Routes are updating…"; после перехода в known-state применяется MP-1b поведение (сброс недоступного To и warning).

---

## Итоговая таблица

| №  | Пункт ревью | Приоритет | Статус |
|----|-------------|-----------|--------|
| P0.1 | Multi-hop targets vs direct canSendPayment | P0 | ✅ Исправлено |
| P0.2 | Текст reverse debt warning | P0 | ✅ Исправлено |
| P1.3 | `'0'` capacity как unknown в `toOptionLabel` | P1 | ✅ Исправлено |
| P1.4 | Debt-hint в EdgeDetailPopup — оба долга | P1 | ✅ Исправлено |
| P1.5 | A11y: progressbar unknown → `aria-valuenow=0` | P1 | ✅ Исправлено |
| P1.6 | NC-2: видимый `avail:` префикс в NodeCardOverlay | P1 | ✅ Исправлено |
| P2.7 | Toast таймер при повторе текста (реализация) | P2 | ✅ Исправлено |
| P2.7 | Toast таймер при повторе текста (тесты) | P2 | ✅ Добавлено |
| P2.8 | «Тихий busy» после cancel | P2 | ✅ Реализовано (UI-объяснение) |
| P2.9 | Тест: ClearingPanel preview-loading | P2 | ✅ Добавлено |
| P2.9 | Тест: root tri-state wiring | P2 | ✅ Добавлено |

---

## Резюме

Все **P0 и P1** замечания корректно реализованы в коде. Семантический конфликт targets/capacity устранён через `PAYMENT_TARGETS_MAX_HOPS=1`; все текстовые предупреждения для долга (TrustlineManagementPanel и EdgeDetailPopup) теперь правильно различают `used` и `reverse`; баг `'0'`-as-unknown исправлен; A11y progressbar корректен; NC-2 отображает `avail:` видимо.

Из **P2** замечаний: добавлены тесты на повтор одинакового toast-текста (через microtask reset), на корректное управление toast-таймерами (unmount/dismiss), устранён «тихий busy» в UI через явные подсказки/tooltip, и закрыты недостающие тесты по ClearingPanel preview-loading и root tri-state wiring.
