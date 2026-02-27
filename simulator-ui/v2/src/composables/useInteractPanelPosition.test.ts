import { nextTick, ref } from 'vue'
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

  it('startFlowFromNodeCard pattern: snapshot set synchronously after phase transition', () => {
    // Simulates the exact sequence from startFlowFromNodeCard:
    // 1. snapshot = snapshotNodeCenter()   → {x:500, y:300}
    // 2. phase idle → picking-payment-from (sync watcher clears anchor)
    // 3. phase picking-payment-from → picking-payment-to (same group, anchor preserved)
    // 4. openPanelFrom('node-card', snapshot)  — synchronous, no nextTick needed
    //    (flush:'sync' watcher already fired during steps 2-3)

    const phase = ref<InteractPhase>('idle')
    const h = useInteractPanelPosition(phase)

    const snapshot = { x: 500, y: 300 }

    // step 2: startPaymentFlow()
    phase.value = 'picking-payment-from'   // idle→payment: group changes, watcher clears anchor
    expect(h.panelAnchor.value).toBeNull()

    // step 3: setPaymentFromPid(pid)  
    phase.value = 'picking-payment-to'     // payment→payment: same group, no clear
    expect(h.panelAnchor.value).toBeNull() // still null (never set yet)

    // step 4: synchronous open (no nextTick needed — sync watcher already done)
    h.openFrom('node-card', snapshot)

    expect(h.panelAnchor.value).toStrictEqual({ x: 500, y: 300 })

    // Further sub-phase transitions should NOT clear it
    phase.value = 'confirm-payment'
    expect(h.panelAnchor.value).toStrictEqual({ x: 500, y: 300 })
  })

  it('startFlowFromNodeCard pattern: anchor persists after multiple nextTicks', async () => {
    const phase = ref<InteractPhase>('idle')
    const h = useInteractPanelPosition(phase)

    const snapshot = { x: 800, y: 400 }

    // Simulate flow
    phase.value = 'picking-payment-from'
    phase.value = 'picking-payment-to'

    // Set synchronously
    h.openFrom('node-card', snapshot)
    expect(h.panelAnchor.value).toStrictEqual({ x: 800, y: 400 })

    // Wait ticks — anchor should still be there
    await nextTick()
    expect(h.panelAnchor.value).toStrictEqual({ x: 800, y: 400 })

    await nextTick()
    expect(h.panelAnchor.value).toStrictEqual({ x: 800, y: 400 })
  })
})
