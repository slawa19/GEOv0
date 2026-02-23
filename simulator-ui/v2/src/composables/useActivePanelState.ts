import { computed, unref, type ComputedRef, type Ref } from 'vue'

import type { InteractPhase } from './useInteractMode'

export type ActivePanelKey = 'payment' | 'trustline' | 'clearing'
export type ActivePanelKeyOrNull = ActivePanelKey | null

type PhaseLike = InteractPhase | null | undefined

/**
 * Single source of truth for "which Interact panel is currently active".
 *
 * Historically:
 * - SimulatorAppRoot used `activePanelType`
 * - ActionBar used `activeKey`
 *
 * Both were derived from the Interact phase string. This composable consolidates
 * the mapping and keeps both names to avoid a larger refactor (H6).
 */
export function useActivePanelState(
  phase: Ref<PhaseLike> | ComputedRef<PhaseLike> | PhaseLike,
): {
  activePanelType: ComputedRef<ActivePanelKeyOrNull>
  activeKey: ComputedRef<ActivePanelKeyOrNull>
} {
  const phaseNorm = computed(() => String(unref(phase) ?? '').toLowerCase())

  const activeKey = computed<ActivePanelKeyOrNull>(() => {
    const p = phaseNorm.value
    if (p.includes('payment')) return 'payment'
    if (p.includes('trustline')) return 'trustline'
    if (p.includes('clearing')) return 'clearing'
    return null
  })

  // Alias for backward-compatible naming in the root component.
  const activePanelType = activeKey

  return { activePanelType, activeKey }
}

