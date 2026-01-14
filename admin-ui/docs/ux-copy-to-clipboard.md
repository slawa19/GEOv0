# UX: copy-to-clipboard

Admin UI has a small reusable copy-to-clipboard button used across tables and drawers.

## Building blocks
- Clipboard helper: `src/utils/copyToClipboard.ts`
- UI component: `src/ui/CopyIconButton.vue`

## Usage
Place the button next to any PID / TxID / Equivalent / Object ID.

Examples:
- Table cells: put the button after the value, and let the button stop row-click propagation.
- Drawers/descriptions: put the button after the text/link for quick copy.

## Behavior
- Uses `navigator.clipboard.writeText()` when available (secure context).
- Falls back to a hidden `<textarea>` + `document.execCommand('copy')`.
- Shows success/error via `ElMessage`.
