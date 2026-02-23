import { ref } from 'vue'
import { describe, expect, it } from 'vitest'

import type { InteractPhase } from './useInteractMode'
import { useInteractPanelPosition } from './useInteractPanelPosition'

describe('useInteractPanelPosition', () => {
  it('defaults snapshot to null and resets on phase change', () => {
    const phase = ref<InteractPhase>('idle')
    const h = useInteractPanelPosition(phase)

    h.openFrom('action-bar')
    expect(h.panelAnchor.value).toBeNull()

    h.openFrom('node-card', { x: 10, y: 20 })
    expect(h.panelAnchor.value).toStrictEqual({ x: 10, y: 20 })

    phase.value = 'confirm-payment'
    expect(h.panelAnchor.value).toBeNull()
  })
})
