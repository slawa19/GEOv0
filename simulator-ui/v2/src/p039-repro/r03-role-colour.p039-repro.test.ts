/**
 * 039 T3901 - R3 (colour language), MAPPING tier.
 *
 * Corrected form (P-1, P-2): the role of a node comes from the snapshot's authoritative `net_sign`
 * (+1 net creditor, -1 net debtor, 0 neutral, null / absent = unknown) and `status` (a freeze wins over the role).
 * There are no `used_out` / `used_in` on a node. A hub mark is independent of the role.
 *
 * What is observed: the fill `fillForNode` gives to nodes that differ ONLY in the field named, with the same
 * `viz_color_key`. The names of the future tokens (`--ds-data-*`) are not asserted here - they do not exist, and a test
 * on a name would be a test on source text; their computed values are the browser tier's
 * (`e2e/qa/p039-repro/r02-r04-legend-tokens-themes.p039-repro.ts`).
 *
 * LIMITS. (1) It judges that roles are told apart, not which hue each gets. (2) "unknown is not zero": only "unknown is
 * not a role" is asserted; whether unknown and neutral share the neutral colour is the spec's to say - measured here.
 * (3) Label colour and the non-colour cue of a role (P-6) are not seen by this tier at all.
 *
 * SLICE B (colour language) must turn the three `red` tests green: then replace `red` by `it`.
 */
import { describe, expect, it } from 'vitest'

import { fillForNode } from '../render/nodeFill'
import type { GraphNode } from '../types'
import { VIZ_MAPPING } from '../vizMapping'
import { fixture100, measured, red } from './p039'

const base: GraphNode = { id: 'n', name: 'N', type: 'person', status: 'active', viz_color_key: 'person', viz_shape_key: 'circle' }
const fill = (over: Partial<GraphNode>) => fillForNode({ ...base, ...over }, VIZ_MAPPING)

describe('039 R3 - the colour of a node says its net role (inputs: net_sign, status)', () => {
  it('counter-check: the mapping answers a colour for every case the reproducers use', () => {
    // Nothing here requires a colour per KIND of participant: after slice B an unknown role looks the same for a person and a business.
    for (const over of [{}, { net_sign: 1 as const }, { net_sign: -1 as const }, { net_sign: 0 as const }, { net_sign: null }, { status: 'frozen' }, { type: 'business', viz_color_key: 'business' }]) {
      expect(fill(over), JSON.stringify(over)).toMatch(/^(#[0-9a-f]{3,8}|rgba?\(.+\)|hsla?\(.+\))$/i)
    }
  })

  red('net creditor (+1) and net debtor (-1) of the same kind get different colours (today: net_sign is not read)', 'creditor vs debtor', () => {
    const row = { creditor: fill({ net_sign: 1 }), debtor: fill({ net_sign: -1 }), neutral: fill({ net_sign: 0 }), unknown: fill({ net_sign: null }), absent: fill({}) }
    measured('R3 fills by net_sign, same viz_color_key', row)
    expect(row.creditor, 'creditor vs debtor').not.toBe(row.debtor)
    expect(row.neutral, 'neutral is neither role: vs creditor').not.toBe(row.creditor)
    expect(row.neutral, 'neutral is neither role: vs debtor').not.toBe(row.debtor)
    expect([row.creditor, row.debtor], 'unknown (null) is not shown as a role').not.toContain(row.unknown)
    expect([row.creditor, row.debtor], 'an absent net_sign is not shown as a role').not.toContain(row.absent)
  })

  red('the same role gets the same colour whatever the kind of participant and its debt bin (today: 15 keys)', 'distinct colours among three net debtors', () => {
    const debtors = [fill({ net_sign: -1, viz_color_key: 'debt-2' }), fill({ net_sign: -1, viz_color_key: 'debt-8' }), fill({ net_sign: -1, type: 'business', viz_color_key: 'business' })]
    measured('R3 fills of three net debtors', debtors)
    expect(new Set(debtors).size, 'distinct colours among three net debtors').toBe(1)
  })

  red('a freeze wins over the role: a frozen creditor and a frozen debtor look the same, and unlike both roles', 'not the colour of a role', () => {
    // The fixture and the pipeline name the state `frozen` (snapshot) / `suspended` (after a topology event); both are a freeze.
    for (const status of ['frozen', 'suspended']) {
      const frozenCreditor = fill({ status, net_sign: 1 })
      const frozenDebtor = fill({ status, net_sign: -1 })
      expect(frozenCreditor, `status ${status}: same colour for both roles`).toBe(frozenDebtor)
      expect([fill({ net_sign: 1 }), fill({ net_sign: -1 })], `status ${status}: not the colour of a role`).not.toContain(frozenCreditor)
    }
  })

  it('measurement (dated 2026-10-12, base 90e17a4c; not an acceptance condition): what the shipped 100-node fixture gives the mapping (role inputs and colour keys)', () => {
    const snap = fixture100()
    const count = (f: (n: GraphNode) => string) => snap.nodes.reduce<Record<string, number>>((m, n) => ({ ...m, [f(n)]: (m[f(n)] ?? 0) + 1 }), {})
    const fills = new Set(snap.nodes.map((n) => fillForNode(n, VIZ_MAPPING)))
    const fillsByRole = (sign: number) => new Set(snap.nodes.filter((n) => n.net_sign === sign).map((n) => fillForNode(n, VIZ_MAPPING))).size
    const fields = [...new Set(snap.nodes.flatMap((n) => Object.keys(n)))].sort()
    measured('R3 fixture role inputs', {
      nodes: snap.nodes.length, netSign: count((n) => String(n.net_sign)), status: count((n) => String(n.status)), type: count((n) => String(n.type)),
      colourKeyByNetSign: count((n) => `${String(n.net_sign)}:${String(n.viz_color_key)}`),
      distinctFills: fills.size, distinctFillsAmongCreditors: fillsByRole(1), distinctFillsAmongDebtors: fillsByRole(-1),
      mappingKeys: Object.keys(VIZ_MAPPING.node.color).length, nodeFields: fields,
      // P-1: "the hub mark comes from an existing authoritative field". Which node fields could carry it today:
      hubFieldCandidates: fields.filter((f) => /hub|links_count|degree|role/i.test(f)),
    })
    expect(snap.nodes).toHaveLength(100)
    // No bound on the number of fills: after slice B the fixture needs at most four (creditor, debtor, neutral, frozen).
  })
})
