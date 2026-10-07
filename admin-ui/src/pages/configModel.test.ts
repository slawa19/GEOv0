import { describe, expect, it } from 'vitest'

import { buildPatch, dirtyKeysOf, kindOf, sectionForKey, toRows, unitHintKey } from './configModel'

describe('configModel', () => {
  it('gives a value the control of its type, and structured values their JSON text', () => {
    expect([true, 3, 'x', { a: 1 }, null].map(kindOf)).toEqual(['boolean', 'number', 'string', 'json', 'json'])
    expect(toRows({ B: 1, A: { x: [1] } })).toEqual([
      { key: 'A', kind: 'json', value: JSON.stringify({ x: [1] }, null, 2) },
      { key: 'B', kind: 'number', value: 1 },
    ])
  })

  it('files a key under its section, and an unknown mutable key under "other"', () => {
    expect(sectionForKey('CLEARING_ENABLED')).toBe('featureFlags')
    expect(sectionForKey('FEATURE_FLAGS_MULTIPATH_ENABLED')).toBe('featureFlags')
    expect(sectionForKey('RATE_LIMIT_ENABLED')).toBe('rateLimit')
    expect(sectionForKey('ROUTING_MAX_HOPS')).toBe('routing')
    expect(sectionForKey('SOMETHING_NEW')).toBe('other')
  })

  it('derives a unit hint from the name', () => {
    expect(unitHintKey('X_SECONDS')).toBe('config.helpFallback.units.seconds')
    expect(unitHintKey('ROUTING_MAX_HOPS')).toBe('config.helpFallback.units.count')
    expect(unitHintKey('CLEARING_ENABLED')).toBeNull()
  })

  it('finds exactly the rows that changed, comparing a structured value by its text', () => {
    const original = { FLAG: true, N: 3, J: { a: 1 } }
    const rows = toRows(original)
    expect(dirtyKeysOf(rows, original)).toEqual([])
    const edited = rows.map((r) => (r.key === 'N' ? { ...r, value: 4 } : r))
    expect(dirtyKeysOf(edited, original)).toEqual(['N'])
  })

  it('builds the patch of the changed keys, parsing structured values, and refuses broken JSON as a whole', () => {
    const rows = [
      { key: 'N', kind: 'number' as const, value: 4 },
      { key: 'J', kind: 'json' as const, value: '{"a": 2}' },
      { key: 'UNTOUCHED', kind: 'string' as const, value: 'x' },
    ]
    expect(buildPatch(rows, ['N', 'J'])).toEqual({ patch: { N: 4, J: { a: 2 } } })
    expect(buildPatch([rows[0]!, { key: 'J', kind: 'json', value: '{broken' }], ['N', 'J'])).toEqual({ invalidJsonKey: 'J' })
  })
})
