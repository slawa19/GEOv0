import { describe, expect, it, vi } from 'vitest'

import type { Participant, Trustline } from '../../types/domain'
import { buildGraphElements, type GraphElementsInput } from './graphElements'
import { NODE_COLOR_BY_VIZ_KEY, selectionBorderColor } from './graphStyle'

// The element builder is pure: if anything on its import path loaded Cytoscape at run time, this fails the file.
vi.mock('cytoscape', () => {
  throw new Error('graphElements must not load cytoscape')
})

function person(pid: string, over: Partial<Participant> = {}): Participant {
  return { pid, display_name: pid.toLowerCase(), type: 'person', status: 'active', ...over }
}

function line(from: string, to: string, over: Partial<Trustline> = {}): Trustline {
  return {
    equivalent: 'EUR',
    from,
    to,
    limit: '10.00',
    used: '2.00',
    available: '8.00',
    status: 'active',
    created_at: '2026-01-01T00:00:00Z',
    ...over,
  }
}

function build(over: Partial<GraphElementsInput> = {}) {
  return buildGraphElements({
    participants: [],
    trustlines: [],
    typeFilter: [],
    minDegree: 0,
    hideIsolates: false,
    focus: { enabled: false, rootPid: '', depth: 1 },
    focusedPid: '',
    threshold: '0.10',
    ...over,
  })
}

const pids = (r: ReturnType<typeof build>) => r.nodes.map((n) => n.data?.pid).sort()

describe('buildGraphElements: nodes and isolates', () => {
  const participants = [person('A'), person('B'), person('LONE'), person('C', { type: 'business' })]

  it('shows true isolates unless asked to hide them', () => {
    const trustlines = [line('A', 'B')]
    expect(pids(build({ participants, trustlines }))).toEqual(['A', 'B', 'C', 'LONE'])
    expect(pids(build({ participants, trustlines, hideIsolates: true }))).toEqual(['A', 'B'])
  })

  it('does not turn a node whose only lines go to a hidden type into an isolate', () => {
    // C (business) has a line to A. With the type filter on person, C is hidden; A is not an isolate (it has a
    // line, to a hidden type), so `hideIsolates: false` must not add it as a lone node.
    const r = build({ participants, trustlines: [line('A', 'C')], typeFilter: ['person'] })
    expect(pids(r)).toEqual(['B', 'LONE'])
    expect(r.edges).toHaveLength(0)
  })

  it('keeps a cross-type line when both of its types are allowed, and drops it when one is not', () => {
    const trustlines = [line('A', 'C')]
    expect(build({ participants, trustlines, typeFilter: ['person', 'business'] }).edges).toHaveLength(1)
    expect(build({ participants, trustlines, typeFilter: ['business'] }).edges).toHaveLength(0)
  })

  it('compares types case-insensitively and treats a participant without a type as filtered out', () => {
    const odd = [person('A', { type: 'Person' }), person('B', { type: '' })]
    expect(pids(build({ participants: odd, typeFilter: ['PERSON'] }))).toEqual(['A'])
  })

  it('drops nodes below the minimum degree except the participant the search focused', () => {
    const ps = [person('HUB'), person('X'), person('Y'), person('Z')]
    const trustlines = [line('HUB', 'X'), line('HUB', 'Y'), line('Y', 'Z')]
    expect(pids(build({ participants: ps, trustlines, minDegree: 2 }))).toEqual(['HUB', 'Y'])
    expect(pids(build({ participants: ps, trustlines, minDegree: 2, focusedPid: 'Z' }))).toEqual(['HUB', 'Y', 'Z'])
  })
})

describe('buildGraphElements: focus mode', () => {
  const ps = ['R', 'N1', 'N2', 'FAR', 'ALONE'].map((p) => person(p))
  const trustlines = [line('R', 'N1'), line('N1', 'N2'), line('N2', 'FAR')]

  it('keeps the root and its neighbourhood to the asked depth, along a line in either direction', () => {
    const d1 = build({ participants: ps, trustlines, focus: { enabled: true, rootPid: 'N1', depth: 1 } })
    expect(pids(d1)).toEqual(['N1', 'N2', 'R'])
    const d2 = build({ participants: ps, trustlines, focus: { enabled: true, rootPid: 'R', depth: 2 } })
    expect(pids(d2)).toEqual(['N1', 'N2', 'R'])
  })

  it('keeps a root that has no line, and ignores isolate hiding and the minimum degree', () => {
    const r = build({
      participants: ps,
      trustlines,
      focus: { enabled: true, rootPid: 'ALONE', depth: 2 },
      minDegree: 5,
      hideIsolates: true,
    })
    expect(pids(r)).toEqual(['ALONE'])
  })

  it('shows nothing for a root that is filtered out by type', () => {
    const r = build({
      participants: [person('R'), person('N1', { type: 'business' })],
      trustlines: [line('R', 'N1')],
      typeFilter: ['business'],
      focus: { enabled: true, rootPid: 'R', depth: 1 },
    })
    expect(pids(r)).toEqual([])
  })
})

describe('buildGraphElements: bottlenecks', () => {
  const participants = [person('A'), person('B')]
  const flags = (trustlines: Trustline[], threshold = '0.10') =>
    build({ participants, trustlines, threshold }).edges.map((e) => [e.data?.bottleneck, String(e.classes).includes('bottleneck')])

  it('marks an active line whose available share is below the threshold', () => {
    expect(flags([line('A', 'B', { limit: '10.00', available: '0.50' })])).toEqual([[1, true]])
    expect(flags([line('A', 'B', { limit: '10.00', available: '8.00' })])).toEqual([[0, false]])
  })

  it('does not mark a line exactly at the threshold, nor a closed line, and follows the threshold', () => {
    expect(flags([line('A', 'B', { limit: '10.00', available: '1.00' })])).toEqual([[0, false]])
    expect(flags([line('A', 'B', { limit: '10.00', available: '0.50', status: 'closed' })])).toEqual([[0, false]])
    expect(flags([line('A', 'B', { limit: '10.00', available: '3.00' })], '0.5')).toEqual([[1, true]])
  })
})

describe('buildGraphElements: element data', () => {
  it('normalizes the equivalent in the edge and its id, and carries the line as the drawer reads it', () => {
    const r = build({
      participants: [person('A'), person('B')],
      trustlines: [line('A', 'B', { equivalent: ' eur ', close_requested_at: '2026-02-01T00:00:00Z' })],
    })
    const edge = r.edges[0]!
    expect(edge.data).toMatchObject({
      id: 'tl_0_A_B_EUR',
      source: 'A',
      target: 'B',
      equivalent: 'EUR',
      status: 'active',
      limit: '10.00',
      used: '2.00',
      available: '8.00',
      close_requested_at: '2026-02-01T00:00:00Z',
    })
    expect(edge.classes).toBe('tl-active')
  })

  it('takes the node colour from the status first, then the server key, then the type', () => {
    const r = build({
      participants: [
        person('FROZEN', { status: 'frozen', viz_color_key: 'debt-8' }),
        person('DEBTOR', { viz_color_key: 'debt-8' }),
        person('SHOP', { type: 'business' }),
        person('ODD', { type: 'hub' }),
      ],
    })
    const contour = (pid: string) => r.nodes.find((n) => n.data?.pid === pid)?.data?.sel_border_color
    expect(contour('FROZEN')).toBe(selectionBorderColor(NODE_COLOR_BY_VIZ_KEY.suspended!))
    expect(contour('DEBTOR')).toBe(selectionBorderColor(NODE_COLOR_BY_VIZ_KEY['debt-8']!))
    expect(contour('SHOP')).toBe(selectionBorderColor(NODE_COLOR_BY_VIZ_KEY.business!))
    // A type with no colour of its own gets the default, not the colour of another key.
    expect(contour('ODD')).toBe(selectionBorderColor('#409eff'))
    // The contours really differ - the checks above are not four copies of one value.
    expect(new Set(['FROZEN', 'DEBTOR', 'SHOP', 'ODD'].map(contour)).size).toBe(4)
  })

  it('classes a node by status, type and server colour key, and sizes it by the server or by its type', () => {
    const r = build({
      participants: [
        person('P', { status: 'Suspended', viz_color_key: 'debt-3', viz_size: { w: 30, h: 12 } }),
        person('B', { type: 'business' }),
        person('Q'),
      ],
    })
    const node = (pid: string) => r.nodes.find((n) => n.data?.pid === pid)!
    expect(node('P').classes).toBe('p-suspended type-person viz-debt-3')
    expect([node('P').data?.viz_w, node('P').data?.viz_h]).toEqual([30, 12])
    expect([node('B').data?.viz_w, node('B').data?.viz_h]).toEqual([26, 22])
    expect([node('Q').data?.viz_w, node('Q').data?.viz_h]).toEqual([16, 16])
    expect(node('Q').data?.label).toBe('')
  })

  it('builds nothing from nothing', () => {
    expect(build()).toEqual({ nodes: [], edges: [] })
    expect(build({ participants: null })).toEqual({ nodes: [], edges: [] })
  })
})
