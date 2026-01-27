import { describe, expect, it } from 'vitest'
import { validateEvents, validateSnapshot } from './fixtures'

function mkBaseSnapshot() {
  return {
    equivalent: 'UAH',
    generated_at: '2026-01-27T00:00:00Z',
    nodes: [
      { id: 'A', type: 'person', viz_color_key: 'person', viz_size: { w: 10, h: 10 } },
      { id: 'B', type: 'business', viz_color_key: 'business', viz_size: { w: 14, h: 14 } },
    ],
    links: [
      {
        source: 'A',
        target: 'B',
        trust_limit: 10,
        used: '2',
        available: 8,
        viz_width_key: 'thin',
        viz_alpha_key: 'bg',
      },
    ],
  }
}

describe('fixtures validation', () => {
  it('validateSnapshot accepts a minimal valid snapshot', () => {
    const raw = mkBaseSnapshot()
    const snap = validateSnapshot(raw, 'mem:snapshot.json')
    expect(snap.nodes).toHaveLength(2)
    expect(snap.links).toHaveLength(1)
    expect(snap.links[0]).toMatchObject({ source: 'A', target: 'B', trust_limit: 10, used: '2', available: 8 })
  })

  it('validateSnapshot throws on unknown viz keys in strict mode', () => {
    const raw = mkBaseSnapshot()
    raw.nodes[0]!.viz_color_key = 'unknown-color-key'
    expect(() => validateSnapshot(raw, 'mem:snapshot.json')).toThrow(/Unknown viz_color_key/)
  })

  it('validateSnapshot drops non-number/non-string amounts without throwing', () => {
    const raw = mkBaseSnapshot()
    ;(raw.links[0] as any).trust_limit = { bad: true }
    ;(raw.links[0] as any).used = NaN
    ;(raw.links[0] as any).available = Infinity

    const snap = validateSnapshot(raw, 'mem:snapshot.json')
    expect(snap.links[0]!.trust_limit).toBeUndefined()
    expect(snap.links[0]!.used).toBeUndefined()
    expect(snap.links[0]!.available).toBeUndefined()
  })

  it('validateEvents accepts a minimal tx.updated playlist', () => {
    const raw = [
      {
        event_id: 'evt-1',
        ts: '2026-01-27T00:00:00Z',
        type: 'tx.updated',
        equivalent: 'UAH',
        ttl_ms: 250,
        edges: [{ from: 'A', to: 'B' }],
      },
    ]

    const events = validateEvents(raw, 'mem:events.json')
    expect(events).toHaveLength(1)
    expect(events[0]!.type).toBe('tx.updated')
  })
})
