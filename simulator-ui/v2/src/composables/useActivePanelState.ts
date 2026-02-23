import { computed, inject, provide, unref, type ComputedRef, type InjectionKey, type Ref } from 'vue'

import type { InteractPhase } from './useInteractMode'
import { toLower } from '../utils/stringHelpers'

export type ActivePanelKey = 'payment' | 'trustline' | 'clearing'
export type ActivePanelKeyOrNull = ActivePanelKey | null

type PhaseLike = InteractPhase | null | undefined

export type ActivePanelState = {
  activePanelType: ComputedRef<ActivePanelKeyOrNull>
  activeKey: ComputedRef<ActivePanelKeyOrNull>
}

const ACTIVE_PANEL_STATE_KEY: InjectionKey<ActivePanelState> = Symbol('geo.activePanelState')

function createActivePanelState(
  phase: Ref<PhaseLike> | ComputedRef<PhaseLike> | PhaseLike,
): ActivePanelState {
  const phaseNorm = computed(() => toLower(unref(phase)))

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

export function provideActivePanelState(
  phase: Ref<PhaseLike> | ComputedRef<PhaseLike> | PhaseLike,
): ActivePanelState {
  const state = createActivePanelState(phase)
  provide(ACTIVE_PANEL_STATE_KEY, state)
  return state
}

/**
 * Prefer injected state when available (single instance for a subtree),
 * otherwise fall back to a local derived instance.
 */
export function useActivePanelStateShared(
  phase: Ref<PhaseLike> | ComputedRef<PhaseLike> | PhaseLike,
): ActivePanelState {
  return inject(ACTIVE_PANEL_STATE_KEY, null) ?? createActivePanelState(phase)
}

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
  return createActivePanelState(phase)
}

