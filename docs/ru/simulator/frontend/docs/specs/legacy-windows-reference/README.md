# Legacy windows markup reference (WM-only runtime)

Этот каталог — **текстовый референс DOM/верстки** для окон/панелей симулятора.

## Что это

- Мы фиксируем **HTML-разметку и классы** ключевых окон (node-card / edge-detail / interact panels) через snapshot-тест.
- Это **не runtime UI** и **не поведенческие тесты**: без backend, без реальных данных, без проверок логики.

## Что не фиксируем

- Позиционирование/геометрию (left/top), размеры и layout на экране.
- Поведение ESC/outside-click и любые сценарии interact flow.

## Где лежит тест

- Snapshot-тест: `simulator-ui/v2/src/legacyReference/legacyWindowsMarkupSnapshots.test.ts`
- Snapshot-файлы: создаются Vitest рядом (папка `__snapshots__`).

## Как рендерим (важно)

- Все компоненты рендерятся в единственном поддерживаемом (WM-only) варианте — без legacy self-positioning.
- Props/состояния заданы минимально, чтобы отрисовался «типовой» DOM.

## Как обновить снапшоты

```bash
npm --prefix simulator-ui/v2 run test:unit -- -u
```

## Компоненты-источники

- `simulator-ui/v2/src/components/NodeCardOverlay.vue`
- `simulator-ui/v2/src/components/EdgeDetailPopup.vue`
- `simulator-ui/v2/src/components/ManualPaymentPanel.vue`
- `simulator-ui/v2/src/components/TrustlineManagementPanel.vue`
- `simulator-ui/v2/src/components/ClearingPanel.vue`

> Дата фиксации: 2026-03-03
