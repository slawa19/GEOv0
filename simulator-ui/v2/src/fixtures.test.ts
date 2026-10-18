import { describe, expect, it, vi } from 'vitest'
import { validateSnapshot } from './fixtures'

type RawSnapshot = {
  equivalent: string
  generated_at: string
  nodes: Array<Record<string, unknown>>
  links: Array<Record<string, unknown>>
}

function mkBaseSnapshot(): RawSnapshot {
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
    raw.links[0]!.trust_limit = { bad: true }
    raw.links[0]!.used = NaN
    raw.links[0]!.available = Infinity

    const snap = validateSnapshot(raw, 'mem:snapshot.json')
    expect(snap.links[0]!.trust_limit).toBeUndefined()
    expect(snap.links[0]!.used).toBeUndefined()
    expect(snap.links[0]!.available).toBeUndefined()
  })
})

/**
 * 039 slice B, PR 1: `viz_color_key` stays on the wire and in the versioned fixtures, but it no longer chooses a
 * colour. The loader keeps validating it as INPUT, against the list of admissible keys, and that validation does not
 * depend on any displayed colour (it reads no stylesheet).
 */
describe('fixtures validation - old colour keys still load (039 slice B)', () => {
  const OLD_KEYS = ['unknown', 'business', 'person', 'suspended', 'left', 'deleted', 'debt-0', 'debt-1', 'debt-2', 'debt-3', 'debt-4', 'debt-5', 'debt-6', 'debt-7', 'debt-8']

  it('every key the backend and the fixtures use is accepted on a node (in a recorded node patch: `usePlaylistWorkbench.test.ts`)', () => {
    for (const key of OLD_KEYS) {
      const raw = mkBaseSnapshot()
      raw.nodes[0]!.viz_color_key = key
      expect(validateSnapshot(raw, 'mem:snapshot.json').nodes[0]!.viz_color_key, key).toBe(key)
    }
  })

  it('a key outside the list is still refused: the loader did not stop validating', () => {
    for (const key of ['creditor', 'debtor', 'debt-9', 'hub', 'Person']) {
      const raw = mkBaseSnapshot()
      raw.nodes[0]!.viz_color_key = key
      expect(() => validateSnapshot(raw, 'mem:snapshot.json'), key).toThrow(/Unknown viz_color_key/)
    }
  })

  it('the three versioned demo snapshots load, with their signs as written (null and absent are not turned into zero)', async () => {
    const { readFileSync } = await import('node:fs')
    const { resolve } = await import('node:path')
    for (const eq of ['UAH', 'EUR', 'HOUR']) {
      const raw = JSON.parse(readFileSync(resolve(process.cwd(), `public/simulator-fixtures/v1/${eq}/snapshot.json`), 'utf8')) as RawSnapshot
      const snap = validateSnapshot(raw, `fixture:${eq}`)
      expect(snap.nodes.length, eq).toBe(raw.nodes.length)
      expect(snap.nodes.map((n) => n.net_sign), eq).toEqual(raw.nodes.map((n) => n.net_sign))
    }
    // 039 T3935: the start snapshots the recorded playlists carry (`<EQ>/events/<id>-snapshot.json`, recorded from a live
    // run) load through the same validator - the workbench shows them as the scene while it plays.
    const { readdirSync } = await import('node:fs')
    const starts = readdirSync(resolve(process.cwd(), 'public/simulator-fixtures/v1/UAH/events')).filter((f) => f.endsWith('-snapshot.json'))
    expect(starts.sort()).toEqual(['demo-clearing-snapshot.json', 'demo-tx-snapshot.json'])
    for (const f of starts) {
      const start = JSON.parse(readFileSync(resolve(process.cwd(), `public/simulator-fixtures/v1/UAH/events/${f}`), 'utf8')) as RawSnapshot
      expect(validateSnapshot(start, `fixture:UAH/events/${f}`).nodes.length, f).toBe(100)
    }
    const raw = mkBaseSnapshot()
    raw.nodes[0]!.net_sign = null
    const snap = validateSnapshot(raw, 'mem:snapshot.json')
    expect(snap.nodes[0]!.net_sign).toBeNull()
    expect(snap.nodes[1]!.net_sign).toBeUndefined()
  })

  it('validation reads no stylesheet: it does not depend on the displayed colours', () => {
    const spy = vi.spyOn(window, 'getComputedStyle')
    validateSnapshot(mkBaseSnapshot(), 'mem:snapshot.json')
    expect(spy).not.toHaveBeenCalled()
    spy.mockRestore()
  })
})
