import { ref } from 'vue'
import { describe, expect, it } from 'vitest'

import type { InteractPhase } from './useInteractMode'
import { panelGroupOf, useInteractPanelPosition } from './useInteractPanelPosition'

describe('panelGroupOf', () => {
  it('returns correct group for known phases', () => {
    expect(panelGroupOf('picking-payment-from')).toBe('payment')
    expect(panelGroupOf('picking-payment-to')).toBe('payment')
    expect(panelGroupOf('confirm-payment')).toBe('payment')

    expect(panelGroupOf('picking-trustline-from')).toBe('trustline')
    expect(panelGroupOf('picking-trustline-to')).toBe('trustline')
    expect(panelGroupOf('confirm-trustline-create')).toBe('trustline')
    expect(panelGroupOf('editing-trustline')).toBe('trustline')

    expect(panelGroupOf('confirm-clearing')).toBe('clearing')
    expect(panelGroupOf('clearing-preview')).toBe('clearing')
    expect(panelGroupOf('clearing-running')).toBe('clearing')
  })

  it('returns null for idle and unknown phases', () => {
    expect(panelGroupOf('idle')).toBeNull()
    expect(panelGroupOf('')).toBeNull()
    expect(panelGroupOf('something-else')).toBeNull()
  })
})

describe('useInteractPanelPosition', () => {
  it('defaults snapshot to null and resets on group change', () => {
    const phase = ref<InteractPhase>('idle')
    const h = useInteractPanelPosition(phase)

    h.openFrom('action-bar')
    expect(h.panelAnchor.value).toBeNull()

    h.openFrom('node-card', { x: 10, y: 20 })
    expect(h.panelAnchor.value).toStrictEqual({ x: 10, y: 20 })

    // idle → payment = group change → resets anchor
    phase.value = 'confirm-payment'
    expect(h.panelAnchor.value).toBeNull()
  })

  it('preserves anchor during sub-phase transitions within the same group', () => {
    const phase = ref<InteractPhase>('picking-trustline-from')
    const h = useInteractPanelPosition(phase)

    h.openFrom('node-card', { x: 100, y: 200 })
    expect(h.panelAnchor.value).toStrictEqual({ x: 100, y: 200 })

    // trustline → trustline (sub-phase): anchor preserved
    phase.value = 'picking-trustline-to'
    expect(h.panelAnchor.value).toStrictEqual({ x: 100, y: 200 })

    // trustline → trustline (sub-phase): anchor preserved
    phase.value = 'confirm-trustline-create'
    expect(h.panelAnchor.value).toStrictEqual({ x: 100, y: 200 })

    // trustline → trustline (sub-phase): anchor preserved
    phase.value = 'editing-trustline'
    expect(h.panelAnchor.value).toStrictEqual({ x: 100, y: 200 })
  })

  it('preserves anchor during payment sub-phase transitions', () => {
    const phase = ref<InteractPhase>('picking-payment-from')
    const h = useInteractPanelPosition(phase)

    h.openFrom('node-card', { x: 50, y: 60 })

    phase.value = 'picking-payment-to'
    expect(h.panelAnchor.value).toStrictEqual({ x: 50, y: 60 })

    phase.value = 'confirm-payment'
    expect(h.panelAnchor.value).toStrictEqual({ x: 50, y: 60 })
  })

  it('resets anchor when switching between different flow groups', () => {
    const phase = ref<InteractPhase>('picking-trustline-from')
    const h = useInteractPanelPosition(phase)

    h.openFrom('node-card', { x: 100, y: 200 })

    // trustline → payment = group change → resets anchor
    phase.value = 'picking-payment-from'
    expect(h.panelAnchor.value).toBeNull()
  })

  it('resets anchor when returning to idle', () => {
    const phase = ref<InteractPhase>('confirm-payment')
    const h = useInteractPanelPosition(phase)

    h.openFrom('node-card', { x: 30, y: 40 })

    // payment → idle = group change → resets anchor
    phase.value = 'idle'
    expect(h.panelAnchor.value).toBeNull()
  })
})
