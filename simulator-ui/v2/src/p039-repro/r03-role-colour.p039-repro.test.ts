/**
 * 039 T3901 - R3 (colour language), MAPPING tier. CLOSED by slice B, PR 1 (2026-10-12): the three reproducers that
 * were `red` on the base are plain `it` now, and each was re-read against the design decisions of slice B and
 * STRENGTHENED where the first form left a way round it:
 *
 *  - the first form compared fills with each other only. A mapping that swapped the two roles (`+1` painted as the
 *    debtor) passed it. The roles are now asserted against the NAMES of the data tokens (`DATA_TOKEN`), on a palette
 *    whose five values are made-up and all different - so the test does not know, and does not pin, any hex;
 *  - "unknown is not a role" was all it said about `null` / absent. Decision 2 says more: unknown is filled like
 *    neutral and told from it by its SIGN (`?` against `0`) - asserted through the classifier, the one place a sign
 *    comes from;
 *  - a freeze "wins over the role" was a statement about colour only, and a freeze that erased the role passed. Now:
 *    the fill is the freeze colour AND the sign of the known role is kept;
 *  - `viz_color_key` was held constant, so a mapping that still read it was never asked. Now it is varied, with keys
 *    that used to mean the OPPOSITE role (`debt-8` on a creditor), and the fill must not move.
 *
 * Corrected form (P-1, P-2): the role of a node comes from the snapshot's authoritative `net_sign` (+1 net creditor,
 * -1 net debtor, 0 neutral, null / absent = unknown) and `status` (a freeze wins over the role). There are no
 * `used_out` / `used_in` on a node. A hub mark is independent of the role.
 *
 * LIMITS. (1) Which hue each role gets is not judged here (the tokens are the stylesheet's; their contrast is
 * measured in `visual-language.md`). (2) That the tokens resolve in a browser and are the same in every theme is the
 * browser tier's (`e2e/qa/data-colours.qa.ts`, and `e2e/qa/p039-repro/r02-...`). (3) Label colour is CSS: not seen here.
 */
import { describe, expect, it } from 'vitest'

import { fillForNode } from '../render/nodeFill'
import type { GraphNode } from '../types'
import { classifyNode, createVizMapping, DATA_TOKEN, type DataColorName, type VizMapping } from '../vizMapping'
import { fixture100, measured } from './p039'

/** Made-up, all different: a fill is recognised by WHICH token it is, never by a value this test would have to know. */
const PALETTE = { debtor: '#010101', creditor: '#020202', neutral: '#030303', frozen: '#040404', hub: '#050505', direction: '#060606' } as const
const MAPPING: VizMapping = { ...createVizMapping(), node: { palette: PALETTE } }
const tokenOf = (fill: string): string => {
  const name = (Object.keys(PALETTE) as DataColorName[]).find((k) => PALETTE[k] === fill)
  return name ? DATA_TOKEN[name] : `not a data token: ${fill}`
}

const base: GraphNode = { id: 'n', name: 'N', type: 'person', status: 'active', viz_color_key: 'person', viz_shape_key: 'circle' }
const fill = (over: Partial<GraphNode>) => tokenOf(fillForNode({ ...base, ...over }, MAPPING))
const sign = (over: Partial<GraphNode>) => classifyNode({ ...base, ...over }).sign

describe('039 R3 - the colour of a node says its net role (inputs: net_sign, status)', () => {
  it('counter-check: every case the reproducers use is answered with one of the five data tokens', () => {
    for (const over of [{}, { net_sign: 1 as const }, { net_sign: -1 as const }, { net_sign: 0 as const }, { net_sign: null }, { status: 'frozen' }, { type: 'business', viz_color_key: 'business' }]) {
      expect(fill(over), JSON.stringify(over)).toMatch(/^--ds-data-(debtor|creditor|neutral|frozen|hub)$/)
    }
  })

  it('net creditor (+1) is the CREDITOR token and net debtor (-1) the DEBTOR token - not merely two different colours', () => {
    const row = { creditor: fill({ net_sign: 1 }), debtor: fill({ net_sign: -1 }), neutral: fill({ net_sign: 0 }), unknown: fill({ net_sign: null }), absent: fill({}) }
    measured('R3 fills by net_sign, same viz_color_key', row)
    expect(row.creditor, 'net_sign +1: others owe it more').toBe('--ds-data-creditor')
    expect(row.debtor, 'net_sign -1: it owes more').toBe('--ds-data-debtor')
    expect(row.neutral, 'net_sign 0').toBe('--ds-data-neutral')
    expect(row.unknown, 'unknown (null) is not shown as a role').toBe('--ds-data-neutral')
    expect(row.absent, 'an absent net_sign is not shown as a role').toBe('--ds-data-neutral')
  })

  it('unknown is not zero: null and absent carry the sign "?", zero carries "0" - and the two role signs are not swapped', () => {
    expect(sign({ net_sign: 0 })).toBe('0')
    expect(sign({ net_sign: null })).toBe('?')
    expect(sign({})).toBe('?')
    expect(sign({ net_sign: 1 })).toBe('+')
    expect(sign({ net_sign: -1 })).toBe('−')
  })

  it('the same role gets the same colour whatever the kind of participant and whatever `viz_color_key` says', () => {
    const debtors = [fill({ net_sign: -1, viz_color_key: 'debt-2' }), fill({ net_sign: -1, viz_color_key: 'debt-8' }), fill({ net_sign: -1, type: 'business', viz_color_key: 'business' })]
    measured('R3 fills of three net debtors', debtors)
    expect(new Set(debtors).size, 'distinct colours among three net debtors').toBe(1)
    expect(debtors[0]).toBe('--ds-data-debtor')
    // Keys that used to mean the opposite role, or a status, on a net creditor: the fill does not follow the key.
    for (const key of ['debt-8', 'debt-0', 'suspended', 'left', 'deleted', 'unknown', 'business', null, undefined, 'no-such-key']) {
      expect(fill({ net_sign: 1, viz_color_key: key }), `net creditor with viz_color_key ${String(key)}`).toBe('--ds-data-creditor')
    }
  })

  it('a freeze colours the fill and does NOT erase the role: a frozen creditor and a frozen debtor share the freeze token and keep their signs', () => {
    // The fixture and the pipeline name the state `frozen` (snapshot) / `suspended` (after a topology event); both are a freeze.
    for (const status of ['frozen', 'suspended', ' Frozen ']) {
      expect(fill({ status, net_sign: 1 }), `status ${status}: a frozen creditor`).toBe('--ds-data-frozen')
      expect(fill({ status, net_sign: -1 }), `status ${status}: a frozen debtor`).toBe('--ds-data-frozen')
      expect(sign({ status, net_sign: 1 }), `status ${status}: the creditor's sign is kept`).toBe('+')
      expect(sign({ status, net_sign: -1 }), `status ${status}: the debtor's sign is kept`).toBe('−')
      expect(sign({ status, net_sign: null }), `status ${status}: a freeze does not invent a sign`).toBe('?')
    }
    // Counter-check: a status that is not a freeze does not take the freeze colour.
    for (const status of ['active', 'left', 'deleted', '', undefined]) expect(fill({ status, net_sign: -1 }), `status ${String(status)}`).toBe('--ds-data-debtor')
  })

  it('measurement (dated 2026-10-12, slice B): what the shipped 100-node fixture gives the mapping', () => {
    const snap = fixture100()
    const count = (f: (n: GraphNode) => string) => snap.nodes.reduce<Record<string, number>>((m, n) => ({ ...m, [f(n)]: (m[f(n)] ?? 0) + 1 }), {})
    const byToken = count((n) => tokenOf(fillForNode(n, MAPPING)))
    measured('R3 fixture role inputs', {
      nodes: snap.nodes.length, netSign: count((n) => String(n.net_sign)), status: count((n) => String(n.status)), type: count((n) => String(n.type)),
      fillsByToken: byToken, signs: count((n) => classifyNode(n).sign), hubs: snap.nodes.filter((n) => classifyNode(n).hub).length,
    })
    expect(snap.nodes).toHaveLength(100)
    // The fixture has net creditors, net debtors, even and frozen participants, and no hub: at most four fills.
    expect(Object.keys(byToken).sort()).toEqual(['--ds-data-creditor', '--ds-data-debtor', '--ds-data-frozen', '--ds-data-neutral'])
    // Every active participant with a known sign is painted by that sign - counted from the fixture's own fields.
    const active = (s: number) => snap.nodes.filter((n) => n.status !== 'frozen' && n.net_sign === s).length
    expect(byToken['--ds-data-creditor']).toBe(active(1))
    expect(byToken['--ds-data-debtor']).toBe(active(-1))
  })
})
