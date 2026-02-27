# REVIEW — Manual Operations UI improvements (2026-02-27)

Ревью реализации требований из [`manual-operations-ui-improvements-spec-2026-02-26.md`](../specs/manual-operations-ui-improvements-spec-2026-02-26.md).

Цель ревью: выявить расхождения со спекой и незакрытые риски относительно принципа **UI не предлагает невозможных действий**.

## 1) Executive summary

Реализация Phase 1 и значимой части v2-улучшений выполнена: tri-state targets для To, amount normalization, inline причины блокировок, debt-guards, clearing loading UX, quick actions в popup/overlay, success/error toasts и ESC hint.

Ключевые оставшиеся вопросы — **продуктовое/архитектурное решение по Phase 2.5**: оставлять ли payment targets strict-direct (как сейчас), или включать multi-hop и синхронизировать gating/help-copy.

## 2) Реализовано (по факту кода)

Сводный статус отмечен в спеке в разделе “Implementation status (as of 2026-02-27)”: [`manual-operations-ui-improvements-spec-2026-02-26.md`](../specs/manual-operations-ui-improvements-spec-2026-02-26.md).

Основные места реализации:

- Manual payment: [`ManualPaymentPanel.vue`](simulator-ui/v2/src/components/ManualPaymentPanel.vue:1), [`useParticipantsList.ts`](simulator-ui/v2/src/composables/useParticipantsList.ts:1), [`useInteractMode.ts`](simulator-ui/v2/src/composables/useInteractMode.ts:1), [`SimulatorAppRoot.vue`](simulator-ui/v2/src/components/SimulatorAppRoot.vue:1)
- Trustline: [`TrustlineManagementPanel.vue`](simulator-ui/v2/src/components/TrustlineManagementPanel.vue:1), типы: [`simulatorTypes.ts`](simulator-ui/v2/src/api/simulatorTypes.ts:1)
- Clearing: [`ClearingPanel.vue`](simulator-ui/v2/src/components/ClearingPanel.vue:1)
- Edge/Node overlays: [`EdgeDetailPopup.vue`](simulator-ui/v2/src/components/EdgeDetailPopup.vue:1), [`NodeCardOverlay.vue`](simulator-ui/v2/src/components/NodeCardOverlay.vue:1)
- Feedback: [`SuccessToast.vue`](simulator-ui/v2/src/components/SuccessToast.vue:1), [`ErrorToast.vue`](simulator-ui/v2/src/components/ErrorToast.vue:1), [`ActionBar.vue`](simulator-ui/v2/src/components/ActionBar.vue:1)

## 3) Замечания / техдолг (приоритизация)

### P0 — блокеры принципа “UI не предлагает невозможного” / вводящие в заблуждение

#### P0.1 Multi-hop targets vs direct-hop gating (Manual Payment)

- Спека: tri-state targets и Phase 2.5 backend-first routing: [`MP-1a`](docs/ru/simulator/frontend/docs/specs/manual-operations-ui-improvements-spec-2026-02-26.md:292), [`§7.2`](docs/ru/simulator/frontend/docs/specs/manual-operations-ui-improvements-spec-2026-02-26.md:1208)
- Что не так:
  - Набор To-целей берётся из backend payment-targets, но **сейчас** endpoint вызывается в режиме **direct-only** (`PAYMENT_TARGETS_MAX_HOPS = 1`). Это сделано намеренно, чтобы targets не расходились с текущим gating, который опирается на direct trustline capacity (`availableCapacity`/`canSendPayment`).
- Почему важно: при включении multi-hop в payment-targets без пересмотра gating возможен UX-конфликт: To покажется доступным, но Confirm будет заблокирован как “no direct route”.
- Статус: в текущем коде конфликт предотвращён за счёт direct-only targets; остаётся **техдолг/арх-решение** на Phase 2.5 (multi-hop).
- Следующий шаг (Phase 2.5): либо (A) включить multi-hop targets и убрать direct-only блокировки в gating/help-copy (полагаться на backend), либо (B) оставить direct-only UX как продуктовую семантику.

#### P0.2 Close TL / Close line: тексты предупреждений при reverse debt

- Спека: Phase 2 строгий guard `used || reverse_used`: [`TL-2`](docs/ru/simulator/frontend/docs/specs/manual-operations-ui-improvements-spec-2026-02-26.md:560), [`ED-1`](docs/ru/simulator/frontend/docs/specs/manual-operations-ui-improvements-spec-2026-02-26.md:693)
- Что не так:
  - Ранее warning мог вводить в заблуждение в кейсе `used=0, reverse_used>0`.
  - Сейчас исправлено:
    - Trustline panel показывает debt как `used/reverse` в `closeDebtText`.
    - Edge popup показывает обе величины долга в `closeDebtDisplay`.
- Статус: закрыто (copy теперь соответствует фактической причине блокировки).

### P1 — точечные UX/данные/A11y дефекты

#### P1.1 `capacity === '0'` отображается как unknown `…`

- Спека: capacity label и исключение `0` за счёт фильтрации: [`MP-2`](docs/ru/simulator/frontend/docs/specs/manual-operations-ui-improvements-spec-2026-02-26.md:356)
- Статус: исправлено (используется `cap == null`, строка `'0'` отображается как `0`).

#### P1.2 NodeCardOverlay: формат available column (NC-2)

- Спека: `avail: 150` в явной колонке: [`NC-2`](docs/ru/simulator/frontend/docs/specs/manual-operations-ui-improvements-spec-2026-02-26.md:832)
- Статус: исправлено — `avail:` отображается видимым текстом в колонке (tooltip можно считать дублирующим).

#### P1.3 EdgeDetailPopup utilization bar: unknown vs 0% (A11y)

- Спека: ED-2 utilization bar: [`ED-2`](docs/ru/simulator/frontend/docs/specs/manual-operations-ui-improvements-spec-2026-02-26.md:737)
- Статус: исправлено — при unknown `aria-valuenow` не выставляется, используется `aria-valuetext="unknown"`.

### P2 — надёжность/edge-cases/тесты

#### P2.1 Toast timers: повтор одинакового текста может не перезапустить таймер

- Спека: FB-1/FB-2: [`FB-1`](docs/ru/simulator/frontend/docs/specs/manual-operations-ui-improvements-spec-2026-02-26.md:926), [`FB-2`](docs/ru/simulator/frontend/docs/specs/manual-operations-ui-improvements-spec-2026-02-26.md:980)
- Статус: закрыто.
  - Success: `useInteractMode.setSuccessToastMessage()` сбрасывает значение в `null` и восстанавливает в microtask, чтобы повтор одинакового текста всё равно перезапускал toast.
  - Error: при повторе той же ошибки `runBusy()` аналогично сбрасывает `state.error` в `null` и восстанавливает в microtask.
  - Добавлены unit-тесты: retrigger одинакового текста, очистка таймера на unmount, и корректное поведение при manual dismiss.

#### P2.2 “Тихий busy” после ESC/cancel во время in-flight

- Спека: явного требования нет, но влияет на UX воспринимаемости CL/MP flows.
- Статус: закрыто — ActionBar показывает явный hint `Operation in progress… please wait.` и прокидывает причины disabled в `title` (покрыто unit-тестом).

#### P2.3 Недостающие тесты по спекам

- Phase 2.5 AC для payment-targets: [`AC-MP-15..18`](docs/ru/simulator/frontend/docs/specs/manual-operations-ui-improvements-spec-2026-02-26.md:1280)
- Clearing preview-loading: тест добавлен (ветка preview без данных).
- Root tri-state wiring: тест добавлен (`targets === undefined` при loading и `Set` после).

## 4) Рекомендованный порядок закрытия (строго в рамках этой спеки)

1) Принять решение по P0.1 (семантика: direct-only vs multi-hop в Phase 2.5) и синхронизировать targets/gating/help-copy.
2) Если Phase 2.5 активируется — закрыть AC-MP-15..18.



