import { describe, expect, it } from 'vitest'

import { interactWindowOfPhase } from './interactWindowOfPhase'

describe('interactWindowOfPhase()', () => {
  it('maps payment phases to interact-panel/payment', () => {
    expect(interactWindowOfPhase('picking-payment-from', false)).toEqual({ type: 'interact-panel', panel: 'payment' })
    expect(interactWindowOfPhase('picking-payment-to', false)).toEqual({ type: 'interact-panel', panel: 'payment' })
    expect(interactWindowOfPhase('confirm-payment', false)).toEqual({ type: 'interact-panel', panel: 'payment' })
  })

  it('maps trustline phases to interact-panel/trustline', () => {
    expect(interactWindowOfPhase('picking-trustline-from', false)).toEqual({ type: 'interact-panel', panel: 'trustline' })
    expect(interactWindowOfPhase('picking-trustline-to', false)).toEqual({ type: 'interact-panel', panel: 'trustline' })
    expect(interactWindowOfPhase('confirm-trustline-create', false)).toEqual({ type: 'interact-panel', panel: 'trustline' })
  })

  it('maps editing-trustline to edge-detail unless full editor is requested', () => {
    expect(interactWindowOfPhase('editing-trustline', false)).toEqual({ type: 'edge-detail' })
    expect(interactWindowOfPhase('editing-trustline', true)).toEqual({ type: 'interact-panel', panel: 'trustline' })
  })

  it('maps clearing phases to interact-panel/clearing', () => {
    expect(interactWindowOfPhase('confirm-clearing', false)).toEqual({ type: 'interact-panel', panel: 'clearing' })
    expect(interactWindowOfPhase('clearing-preview', false)).toEqual({ type: 'interact-panel', panel: 'clearing' })
    expect(interactWindowOfPhase('clearing-running', false)).toEqual({ type: 'interact-panel', panel: 'clearing' })
  })

  it('returns null for idle/unknown phases', () => {
    expect(interactWindowOfPhase('idle', false)).toBeNull()
    expect(interactWindowOfPhase('some-new-phase', false)).toBeNull()
  })
})

