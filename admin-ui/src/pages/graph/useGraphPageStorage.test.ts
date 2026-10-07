import { effectScope, ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import { useGraphPageStorage } from './useGraphPageStorage'

/**
 * 032 S7 (adversarial review): with site data blocked, merely reading `window.localStorage` throws. The Graph page
 * must still come up - the stored view settings are a convenience, not a requirement.
 */
describe('useGraphPageStorage with blocked storage', () => {
  it('restores nothing and does not throw, and a later change does not throw either', () => {
    vi.spyOn(window, 'localStorage', 'get').mockImplementation(() => {
      throw new DOMException('blocked', 'SecurityError')
    })
    const input = {
      showLegend: ref(false),
      layoutSpacing: ref(1.5),
      toolbarTab: ref<'filters' | 'display'>('filters'),
      drawerEq: ref('ALL'),
    }
    const scope = effectScope()
    let restore!: () => void
    expect(() => scope.run(() => { restore = useGraphPageStorage(input).restore })).not.toThrow()
    expect(() => restore()).not.toThrow()
    expect(input.layoutSpacing.value).toBe(1.5)
    scope.stop()
  })
})
