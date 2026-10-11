import { describe, expect, it } from 'vitest'

import { isLiveRunState } from './status'

/**
 * 034 S8 (adversarial F5a): the set of live run states is pinned. Three places ask this one rule whether the run's
 * own snapshot is the scene and whether a new Start must first drop the old run; dropping `stopping` or `created`
 * from it survived every other test.
 */
describe('isLiveRunState', () => {
  it.each(['running', 'paused', 'created', 'stopping'])('%s is a live run', (state) => {
    expect(isLiveRunState(state)).toBe(true)
  })

  it.each(['stopped', 'error', '', 'unknown', null, undefined])('%s is not a live run', (state) => {
    expect(isLiveRunState(state)).toBe(false)
  })

  it('the case of the letters does not matter (as before the rule had one owner)', () => {
    expect(isLiveRunState('RUNNING')).toBe(true)
    expect(isLiveRunState('Stopping')).toBe(true)
  })
})
