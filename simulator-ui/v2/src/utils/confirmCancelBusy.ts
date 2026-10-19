import { t } from '../i18n/t'

/**
 * P0-2 policy B: Escape or a click outside while an operation is in flight asks before cancelling it - a synchronous
 * native confirm, kept atomic with the gate. One place for both callers (`useWindowController.ts`, `useSimulatorApp.ts`,
 * which had the same Russian literal in both languages until 046 wave 3); the question is the dictionary's
 * (`common.cancelOperationConfirm`), in the language of the interface. When no confirm can be shown, the cancel is allowed.
 */
export function confirmCancelBusyOperation(): boolean {
  try {
    const c = typeof window !== 'undefined' ? window.confirm : undefined
    if (typeof c !== 'function') return true
    return !!c(t('common.cancelOperationConfirm'))
  } catch {
    return true
  }
}
