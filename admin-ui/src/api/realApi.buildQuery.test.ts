import { describe, expect, it } from 'vitest'

import { buildQuery } from './realApi'

describe('realApi.buildQuery', () => {
  it('builds a relative URL when VITE_API_BASE_URL is empty (vite proxy scenario)', () => {
    const meta = import.meta as unknown as { env: Record<string, unknown> }
    meta.env.VITE_API_BASE_URL = ''

    const url = buildQuery('/api/v1/admin/graph/ego', {
      pid: 'PID_ABC_DEF',
      depth: 2,
      equivalent: 'GEO',
      status: ['active', 'frozen'],
      q: '',
      unused: null,
    })

    expect(url.startsWith('/api/v1/admin/graph/ego')).toBe(true)

    const [, qs = ''] = url.split('?', 2)
    const sp = new URLSearchParams(qs)

    expect(sp.get('pid')).toBe('PID_ABC_DEF')
    expect(sp.get('depth')).toBe('2')
    expect(sp.get('equivalent')).toBe('GEO')
    expect(sp.getAll('status')).toEqual(['active', 'frozen'])

    // Ensure empty/null values are skipped.
    expect(sp.has('q')).toBe(false)
    expect(sp.has('unused')).toBe(false)
  })
})
