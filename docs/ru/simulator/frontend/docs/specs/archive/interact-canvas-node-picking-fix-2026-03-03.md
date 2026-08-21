# Спека: Interact canvas node picking (Payment/Trustline) — фиксы клика и re-pick

**Дата**: 2026-03-03  
**Статус**: DRAFT  
**Scope**: Simulator UI v2 (Interact UI), canvas interactions + Interact FSM  

## 0) Контекст и мотивация

По UX-спеке Interact режима:

- В процессе **Make payment** (окно Manual Payment):
  - **один клик по узлу** должен выбирать участника и заполнять поле **To** (или **From** в соответствующей фазе);
  - это **не должно** закрывать основную карточку узла (NodeCard) и не должно открывать новую;
  - **повторный клик по другому узлу** должен переключать получателя (To) без дополнительных действий пользователя.

Фактическое поведение сейчас:

- один клик по узлу **подсвечивает** узел, но **не заполняет** поле To/From в Interact;
- повторный клик по другому узлу также не меняет To;
- dblclick по узлу продолжает открывать NodeCard (это ок и должно остаться).

## 1) Problem statement (root cause)

Проблема состоит из двух связанных частей.

### 1.1 Drag-to-pin перехватывает pointer-события и подавляет `click`

В текущей связке canvas событий:

- `pointerdown` на ноде уходит в `dragToPin.onPointerDown()` и **возвращает `true`**, тем самым
  - камера не начинает пан;
  - selection выставляется сразу (через `setSelectedNodeId`).
- `pointerup` на ноде уходит в `dragToPin.onPointerUp()` и **всегда возвращает `true`** (даже если фактического drag не было),
  после чего `useCanvasInteractions` вызывает `markSuppressClick()`.
- последующий браузерный `click` **игнорируется**, потому что `suppressNextClick=true`.

Следствие: `onCanvasClick()` (который должен был вызвать Step0-policy и Interact picking) **не выполняется**.

Кодовые точки:

- `useCanvasInteractions.onCanvasPointerUp()` — suppress click при `dragToPin.onPointerUp() === true`.
- `useDragToPinInteraction.onPointerUp()` — возвращает `true` даже для обычного клика (без drag).

### 1.2 Даже при восстановлении `click` отсутствует re-pick To в confirm-payment

`__selectNodeFromCanvasStep0` направляет клик в Interact (`interactSelectNode`) только когда `isInteractPickingPhase=true`.

Сейчас `isPickingPhase` определяется как `state.phase.startsWith('picking-')`, поэтому:

- в `confirm-payment` клики по узлам не считаются «flow input»;
- `useInteractFSM.selectNode()` не обрабатывает `confirm-payment` (нет ветки), поэтому повторный клик не переключает получателя.

## 2) Охват (что затронуто)

### 2.1 Payment

Затронуты все canvas-пикинги Payment:

- `picking-payment-from`
- `picking-payment-to`
- а также требуемый UX: re-pick To в `confirm-payment`.

### 2.2 Trustline (похожий функционал)

Аналогичная проблема воспроизводится и для Trustline flow, потому что он использует тот же canvas click pipeline:

- `picking-trustline-from`
- `picking-trustline-to`

Примечание: Trustline panel показывает подсказки “Pick From/To node (canvas) …”, т.е. UX ожидает работающий picking.

### 2.3 Clearing

Clearing flow в текущей FSM не имеет `picking-*` фаз для выбора участников кликом по узлам (стартует с `confirm-clearing`).
Эта спека clearing не меняет.

## 3) Требуемое поведение (нормативно)

### 3.1 Общие правила кликов

- Single click по узлу:
  - в Interact flow: должен использоваться как input выбора участников;
  - не должен закрывать NodeCard/EdgeDetail (они в другой группе окон);
  - может оставлять визуальную подсветку selected node (не критично), но не должен мешать заполнению полей.

- Double click по узлу:
  - зарезервирован за открытием/обновлением NodeCard;
  - должен продолжать работать как сейчас.

### 3.2 Payment: Make payment

- В `picking-payment-from`: клик по узлу устанавливает `From` и переводит фазу в `picking-payment-to`.
- В `picking-payment-to`: клик по узлу устанавливает `To` и переводит фазу в `confirm-payment`.
- В `confirm-payment`: **клик по другому узлу должен переключать `To`** и оставаться в `confirm-payment`.
  - Если кликнули по тому же узлу, что `From`, поведение: либо игнорировать, либо очищать `To` (решение ниже).

### 3.3 Trustline

- В `picking-trustline-from`: клик по узлу устанавливает `From` и переводит фазу в `picking-trustline-to`.
- В `picking-trustline-to`: клик по узлу устанавливает `To` и переводит в `editing-trustline` или `confirm-trustline-create`.

Дополнительная опция (желательно, но обсудить):

- В `editing-trustline` / `confirm-trustline-create`: single click по узлу переключает `To` (аналогично Payment confirm),
  чтобы пользователь мог быстро пере-выбрать вторую сторону без выхода в picking.

## 4) Предлагаемые изменения (implementation plan)

### 4.1 Исправить подавление `click` для “просто клик” (без drag)

Цель: `suppressNextClick` должен включаться **только если был реальный drag**, а не на каждый click-release по узлу.

Критический нюанс архитектуры событий (важно для реализатора):
- Если узел перехватил `pointerdown` (вернул `true` из `dragToPin.onPointerDown`), он **обязательно** должен перехватить и `pointerup` (вернуть `true` из `dragToPin.onPointerUp`). Если вернуть `false`, `useCanvasInteractions` пробросит "одинокий" `pointerup` в `cameraSystem`, что может сломать её внутренний стейт (т.к. `pointerdown` в камеру не попадал).
- Поэтому менять возвращаемое значение `dragToPin.onPointerUp` на `false` в случае отсутствия drag **нельзя!**
- Правильное решение — проверять флаг `dragState.dragging` **до** вызова `onPointerUp`.

План в `useCanvasInteractions.ts` (функция `onCanvasPointerUp`):
```typescript
      // Захватываем факт драга до того, как pointerUp сбросит стейт
      const wasPinDrag = opts.dragToPin.dragState.dragging;
      if (opts.dragToPin.onPointerUp(ev)) {
        // Подавляем браузерный клик только если было фактическое перемещение
        if (wasPinDrag) {
          markSuppressClick()
        }
        return
      }
```

Затрагиваемые файлы:
- `simulator-ui/v2/src/composables/useCanvasInteractions.ts` — обновить логику `markSuppressClick()` при возврате из `dragToPin.onPointerUp`.
- Доработка внутри `useDragToPinInteraction.ts` не требуется, если внешне читать `dragState.dragging`.

### 4.2 Re-pick To в confirm-payment

Нужно обеспечить, что клик по узлу в `confirm-payment` обновляет `toPid`.

Варианты реализации:

**Вариант A (рекомендуемый):** расширить Step0 gating для canvas node click

- В `useSimulatorApp`: вместо `isInteractPickingPhase` использовать флаг вида `isInteractCanvasNodePickPhase` (или изменить маппинг в геттере),
  который true для:
  - `picking-*` фаз
  - `confirm-payment`
  - (опционально) `editing-trustline`, `confirm-trustline-create`
- Тогда `__selectNodeFromCanvasStep0()` будет направлять клик в `interactSelectNode()`.
- В `useInteractFSM.selectNode()` добавить обработку `confirm-payment`:
  - если `id !== state.fromPid` → `state.toPid = id` (phase остаётся `confirm-payment`)
  - если `id === state.fromPid` → это концептуальная ошибка (To не может быть равен From). Решение: сбрасывать `state.toPid = null` и переводить фазу назад в `picking-payment-to`.

**Вариант B:** “подмена” isPickingPhase

- Изменить `isPickingPhase` так, чтобы включал `confirm-payment`.
- Не рекомендуется: ломает смысл имени, влияет на другие места (cursor, UI hints), усложняет поддержку.

Затрагиваемые файлы:

- `simulator-ui/v2/src/composables/useSimulatorApp.ts`
- `simulator-ui/v2/src/composables/interact/useInteractFSM.ts`

### 4.3 (Опционально) Re-pick To для Trustline edit/create

Если подтверждаем расширение UX для trustline:

- включить `editing-trustline` и `confirm-trustline-create` в `isInteractCanvasNodePickPhase`;
- расширить `useInteractFSM.selectNode()` для этих фаз:
  - если `fromPid` пуст → ставить `fromPid=id`, `toPid=null`, переводить в `picking-trustline-to` (или оставаться, обсуждаемо)
  - если `fromPid` есть → трактовать клик как выбор `toPid` и вызывать `recomputeTrustlinePhase()`.

## 5) Acceptance Criteria

### AC-PAY-1 Single click выбирает To

В Interact UI (`?ui=interact`) при открытом Manual Payment (phase=`picking-payment-to`):

- single click по любому узлу canvas устанавливает `state.toPid` и UI-select “To” показывает выбранного участника;
- NodeCard остаётся открытым (если был открыт), окно Manual Payment остаётся открытым.

### AC-PAY-2 Repeat click переключает To

При phase=`confirm-payment`:

- single click по другому узлу меняет `state.toPid` на новый pid и остаётся в `confirm-payment`;
- введённая сумма (Amount) не теряется.

### AC-TL-1 Trustline picking работает

В Trustline flow:

- `picking-trustline-from`: single click выбирает From;
- `picking-trustline-to`: single click выбирает To и переводит в edit/create фазу.

### AC-DTP-1 Drag-to-pin не ломается

- Обычный click (без перемещения) по узлу НЕ suppress-ит click → `onCanvasClick` отрабатывает.
- Реальный drag (перемещение > threshold) suppress-ит последующий click (чтобы не было нежелательной смены selection/Interact input).

### AC-DBL-1 Dblclick без изменений

- dblclick по узлу продолжает открывать/обновлять NodeCard.

## 6) Test plan

### 6.1 Unit tests

1) `useCanvasInteractions`:
   - сценарий 1 (просто клик): `pointerdown` активирует `dragToPin`, `pointerMove` не переводит в dragging. `useCanvasInteractions.onCanvasPointerUp` вызывает `dragToPin.onPointerUp` (возвращает `true`), но т.к. `dragging === false`, `markSuppressClick` не вызывается. Последующий `onCanvasClick` НЕ подавляется.
   - сценарий 2 (реальный drag): `pointerdown`, затем перемещение (`dragging === true`), `pointerup` вызывает `markSuppressClick`. Последующий `onCanvasClick` должен быть подавлен.

### 6.2 Integration tests (желательные)

В `SimulatorAppRoot.interact.test.ts` добавить сценарий, который проверяет:

- старт Payment с prefilled From (из NodeCard) → phase=`picking-payment-to`
- single click по другому узлу → `state.toPid` обновился и phase=`confirm-payment`
- single click по третьему узлу → `state.toPid` снова обновился (re-pick)

Примечание: важно, чтобы тест реально проходил через pipeline pointerdown/pointerup/click, иначе bug не покрывается.

### 6.3 Manual smoke (локально)

- `Simulator UI (real mode)` → `?ui=interact`
- Открыть NodeCard → Send Payment
- В picking-to:
  - клик по узлу выбирает To
  - повторный клик выбирает другой To
- Подвигать узел (drag-to-pin): после drag не должно происходить непреднамеренное изменение To/From

## 7) Риски и совместимость

- Изменение контракта `dragToPin.onPointerUp()` (семантика boolean) может влиять на подавление клика.
  Нужно убедиться, что единственное место использования — `useCanvasInteractions`.
- Возможны двойные обновления selection (pointerdown устанавливает selected, потом click повторно).
  Это допустимо, но если появится визуальный “фликер”, можно обсудить оптимизацию.
