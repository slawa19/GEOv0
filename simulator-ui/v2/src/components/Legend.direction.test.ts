/**
 * 039 slice B, PR 2 - the legend entry of the direction marker (decision 7 of the slice's design): an arrow and the
 * words `Trust → potential debtor`, shown when the snapshot has trust lines, in the colour the canvas draws the marker.
 *
 * MONEY SEMANTICS: the arrowhead of a line `source -> target` (creditor -> debtor) stands at the debtor. The entry says
 * TRUST and names the end the arrow points at; it never says payment, debt flow or "owes".
 * NOT seen here: what a browser computes for the arrow (`e2e/qa/direction.qa.ts` compares it with canvas pixels).
 */
import { createApp, h, nextTick, reactive, type Component } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import Legend from './Legend.vue'
import { recordingCtx } from '../p039-repro/recordingCtx'
import { drawBaseGraph } from '../render/baseGraph'
import type { GraphNode } from '../types'
import type { LayoutLink, LayoutNode } from '../types/layout'
import { createVizMapping, DATA_PALETTE_DEFAULT, LEGEND_VOCABULARY, linkColorOf, TRUST_DIRECTION_TEXT } from '../vizMapping'

vi.mock('../render/nodePainter', () => ({ drawNodeShape: vi.fn() }))
vi.mock('../render/glowSprites', () => ({ drawGlowSprite: vi.fn() }))

function mount(component: Component, props: Record<string, unknown>) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const app = createApp({ render: () => h(component, props) })
  app.mount(host)
  return { host, done: () => { app.unmount(); host.remove() } }
}

const NODES: GraphNode[] = [
  { id: 'a', name: 'a', type: 'person', status: 'active', viz_shape_key: 'circle', net_sign: 1 },
  { id: 'b', name: 'b', type: 'person', status: 'active', viz_shape_key: 'circle', net_sign: -1 },
]
const base = { nodes: NODES, equivalent: 'UAH', palette: DATA_PALETTE_DEFAULT }
const arrowEntry = (host: HTMLElement) => host.querySelector<HTMLElement>('[data-legend-direction]')

/** "r,g,b" of `#rrggbb` / `rgb()` / `rgba()`. */
function rgbOf(color: string): string {
  const c = color.trim()
  if (c.startsWith('#')) return [1, 3, 5].map((i) => parseInt(c.slice(i, i + 2), 16)).join(',')
  const m = /rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/.exec(c)
  if (!m) throw new Error(`not a colour: "${color}"`)
  return `${m[1]},${m[2]},${m[3]}`
}

describe('Legend - the direction of a trust line', () => {
  it('the words are fixed: trust, and the end the arrow points at - never payment, flow or debt owed', () => {
    expect(TRUST_DIRECTION_TEXT).toBe('Trust → potential debtor')
    expect(TRUST_DIRECTION_TEXT.toLowerCase()).not.toMatch(/pay|flow|owes|owed|sender|receiver/)
  })

  it('shown when the scene has trust lines, as the LAST entry, after the shapes', () => {
    const { host, done } = mount(Legend, { ...base, trustLineColor: '#64748b' })
    const entry = arrowEntry(host)!
    expect(entry, 'the direction entry').not.toBeNull()
    expect(entry.querySelector('.ds-legend__text')!.textContent).toBe('Trust → potential debtor')
    const all = [...host.querySelectorAll('.ds-legend__entry')]
    expect(all[all.length - 1], 'it takes the slot after the shapes').toBe(entry)
    // The entries of participants are untouched: the direction is not one of them.
    expect([...host.querySelectorAll('[data-legend-role]')].map((el) => el.getAttribute('data-legend-role'))).toEqual(['debtor', 'creditor', 'person'])
    expect(LEGEND_VOCABULARY.map((e) => e.id as string)).not.toContain('direction')
    done()
  })

  it('no trust lines (no colour handed over): no direction entry, the rest of the legend stays', () => {
    for (const trustLineColor of [null, undefined, '']) {
      const { host, done } = mount(Legend, { ...base, trustLineColor })
      expect(arrowEntry(host), String(trustLineColor)).toBeNull()
      expect(host.querySelector('[data-testid="legend"]')).not.toBeNull()
      done()
    }
  })

  it('no participants: no legend at all, whatever colour is handed over', () => {
    const { host, done } = mount(Legend, { ...base, nodes: [], trustLineColor: '#64748b' })
    expect(host.textContent).toBe('')
    done()
  })

  it('the arrow POINTS AT the words "potential debtor": shaft from the left, the head at the right end, its tip the rightmost point', () => {
    const { host, done } = mount(Legend, { ...base, trustLineColor: '#64748b' })
    const svg = arrowEntry(host)!.querySelector('svg')!
    expect(svg.getAttribute('aria-hidden')).toBe('true')
    const line = svg.querySelector('line')!
    const head = svg.querySelector('polygon')!
    const pts = head.getAttribute('points')!.trim().split(/\s+/).map((p) => p.split(',').map(Number) as [number, number])
    expect(pts, 'a triangle').toHaveLength(3)
    const tip = pts.reduce((best, p) => (p[0] > best[0] ? p : best))
    const others = pts.filter((p) => p !== tip)
    expect(others.every((p) => p[0] < tip[0]), 'one tip, at the right').toBe(true)
    expect(others[0]![0], 'the base is square to the shaft').toBe(others[1]![0])
    expect(tip[1], 'the tip is on the shaft').toBe(Number(line.getAttribute('y1')))
    expect(Number(line.getAttribute('x1')), 'the shaft starts left of the head').toBeLessThan(others[0]![0])
    expect(Number(line.getAttribute('x2')), 'and does not run past the tip').toBeLessThanOrEqual(tip[0])
    // In the DOM the arrow comes BEFORE the words, and the words end with the debtor: arrow -> "... potential debtor".
    expect(arrowEntry(host)!.firstElementChild!.contains(svg)).toBe(true)
    done()
  })

  it('the shaft is drawn in the line colour it was handed and follows a new one; the head is the marker colour of the palette', async () => {
    const props = reactive({ ...base, trustLineColor: '#aa5500' })
    const host = document.createElement('div')
    document.body.appendChild(host)
    const app = createApp({ render: () => h(Legend, props) })
    app.mount(host)
    const head = () => host.querySelector<SVGElement>('[data-legend-direction] polygon')!
    const shaft = () => host.querySelector<SVGElement>('[data-legend-direction] line')!
    expect(rgbOf(head().getAttribute('fill')!)).toBe(rgbOf(DATA_PALETTE_DEFAULT.direction))
    expect(rgbOf(shaft().getAttribute('stroke')!)).toBe('170,85,0')
    props.trustLineColor = '#010203'
    await nextTick()
    expect(rgbOf(shaft().getAttribute('stroke')!)).toBe('1,2,3')
    props.palette = { ...DATA_PALETTE_DEFAULT, direction: '#040506' }
    await nextTick()
    expect(rgbOf(head().getAttribute('fill')!), 'a new palette object recolours the head').toBe('4,5,6')
    app.unmount()
    host.remove()
  })

  it('canvas and legend agree: the marker `drawBaseGraph` paints is the head of the legend arrow, the body is its shaft - default and snapshot palette', () => {
    const mapping = createVizMapping()
    const a = { id: 'a', __x: 0, __y: 0, viz_shape_key: 'circle', viz_size: { w: 24, h: 24 } } as LayoutNode
    const b = { id: 'b', __x: 200, __y: 0, viz_shape_key: 'circle', viz_size: { w: 24, h: 24 } } as LayoutNode
    for (const [link, palette] of [
      [{ source: 'a', target: 'b', __key: 'a→b' }, undefined],
      [{ source: 'a', target: 'b', __key: 'a→b', viz_color_key: 'default' }, { default: { color: '#112233' } }],
      [{ source: 'a', target: 'b', __key: 'a→b', viz_color_key: 'missing' }, { default: { color: '#112233' } }],
    ] as Array<[LayoutLink, Record<string, { color: string }> | undefined]>) {
      const { ctx, draws } = recordingCtx()
      drawBaseGraph(ctx, { w: 400, h: 200, nodes: [a, b], links: [link], mapping, palette, selectedNodeId: null, activeEdges: new Map(), cameraZoom: 1 })
      const marker = draws.find((d) => d.kind === 'fill')!
      const body = draws.find((d) => d.kind === 'stroke')!
      const legendColor = linkColorOf(link, mapping, palette)
      expect(rgbOf(body.strokeStyle), `key ${String(link.viz_color_key)}: the body`).toBe(rgbOf(legendColor))
      const { host, done } = mount(Legend, { ...base, palette: mapping.node.palette, trustLineColor: legendColor })
      expect(rgbOf(host.querySelector('[data-legend-direction] polygon')!.getAttribute('fill')!), 'the head of the legend arrow is the marker').toBe(rgbOf(marker.fillStyle))
      expect(rgbOf(host.querySelector('[data-legend-direction] line')!.getAttribute('stroke')!), 'the shaft is the body').toBe(rgbOf(body.strokeStyle))
      done()
    }
  })
})

describe('Legend - the frozen swatch shows its dashed outline', () => {
  const frozenScene: GraphNode[] = [...NODES, { id: 'f', name: 'f', type: 'person', status: 'frozen', viz_shape_key: 'circle', net_sign: 1 }]

  it('the outline is a circle of separate dashes round the swatch, and the fill is still the freeze colour of the palette', () => {
    const { host, done } = mount(Legend, { ...base, nodes: frozenScene })
    const swatch = host.querySelector<HTMLElement>('[data-legend-role="frozen"] [data-legend-swatch]')!
    expect(rgbOf(swatch.style.backgroundColor)).toBe(rgbOf(DATA_PALETTE_DEFAULT.frozen))
    const circle = swatch.querySelector('[data-legend-dash] circle')!
    expect(circle, 'the dashed outline').not.toBeNull()
    const [dash, gap] = circle.getAttribute('stroke-dasharray')!.split(/\s+/).map(Number) as [number, number]
    const total = Number(circle.getAttribute('pathLength'))
    expect(dash).toBeGreaterThan(0)
    expect(gap, 'there IS a gap between dashes').toBeGreaterThan(0)
    expect(Number.isInteger(Math.round((total / (dash + gap)) * 1e6) / 1e6), 'a whole number of dashes: no seam where the circle closes').toBe(true)
    // On screen (r = 7 px): each dash and each gap is long enough to be told apart at legend size.
    const unitPx = (2 * Math.PI * Number(circle.getAttribute('r'))) / total
    expect(dash * unitPx, 'dash length in px').toBeGreaterThanOrEqual(2.5)
    expect(gap * unitPx, 'gap length in px').toBeGreaterThanOrEqual(2)
    done()
  })

  it('only the frozen swatch carries it', () => {
    const { host, done } = mount(Legend, { ...base, nodes: frozenScene, trustLineColor: '#64748b' })
    expect(host.querySelectorAll('[data-legend-dash]')).toHaveLength(1)
    expect(host.querySelector('[data-legend-role="creditor"] [data-legend-dash]')).toBeNull()
    done()
  })
})
