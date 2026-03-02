import { describe, expect, it } from 'vitest'

import { buildReloadUrlPreservingWmOptOut } from './navigationUrl'

describe('buildReloadUrlPreservingWmOptOut', () => {
  it('preserves explicit wm=0 even if mut tries to remove it', () => {
    const next = buildReloadUrlPreservingWmOptOut('http://localhost/?mode=real&ui=interact&wm=0', (sp) => {
      sp.set('ui', 'demo')
      sp.delete('wm')
    })

    const u = new URL(next)
    expect(u.searchParams.get('ui')).toBe('demo')
    expect(u.searchParams.get('wm')).toBe('0')
  })

  it('does not introduce wm=0 when it was not explicitly set', () => {
    const next = buildReloadUrlPreservingWmOptOut('http://localhost/?mode=real&ui=interact', (sp) => {
      sp.set('ui', 'demo')
      sp.delete('wm')
    })

    const u = new URL(next)
    expect(u.searchParams.get('ui')).toBe('demo')
    expect(u.searchParams.get('wm')).toBe(null)
  })
})
