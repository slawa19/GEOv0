import type { WindowType } from './types'

export type InteractWindowOfPhaseResult =
  | { type: 'interact-panel'; panel: 'payment' | 'trustline' | 'clearing' }
  | { type: Exclude<WindowType, 'interact-panel'> }

/**
 * Определяет какое окно должно быть открыто для данной фазы.
 *
 * ВАЖНО: `editing-trustline` может показать EdgeDetailPopup (inspector)
 * ИЛИ TrustlineManagementPanel (interact) в зависимости от `isFullEditor`.
 *
 * Нормативный маппинг для MVP: см. [`plans/simulator-window-management-audit.md`](plans/simulator-window-management-audit.md:1128)
 */
export function interactWindowOfPhase(
  phase: string,
  isFullEditor: boolean,
): InteractWindowOfPhaseResult | null {
  switch (String(phase)) {
    case 'picking-payment-from':
    case 'picking-payment-to':
    case 'confirm-payment':
      return { type: 'interact-panel', panel: 'payment' }

    case 'picking-trustline-from':
    case 'picking-trustline-to':
    case 'confirm-trustline-create':
      return { type: 'interact-panel', panel: 'trustline' }

    case 'editing-trustline':
      return isFullEditor
        ? { type: 'interact-panel', panel: 'trustline' }
        : { type: 'edge-detail' }

    case 'confirm-clearing':
    case 'clearing-preview':
    case 'clearing-running':
      return { type: 'interact-panel', panel: 'clearing' }

    default:
      return null
  }
}

