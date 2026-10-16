/**
 * 039 slice B, PR 2 - what `drawBaseGraph` DRAWS for the direction of a trust line (decision 4 of the slice's design).
 *
 * MONEY SEMANTICS (AGENTS.md section 8; `docs/ru/02-protocol-spec.md:505-512`): `source -> target` is creditor ->
 * debtor; the arrowhead stands at the TARGET and shows trust, not a debt and not a payment.
 *
 * Observed: the calls of the edge passes on a recording context (node painting is mocked out). A MARKER here is a
 * `fill` of a closed path of three points. Limits: this is the list of calls, not pixels - whether the triangle can be
 * SEEN is the stand's (`e2e/qa/direction.qa.ts`); the geometry by itself is `linkArrowhead.test.ts`.
 */
import { describe, expect, it, vi } from 'vitest'

import { dist, recordingCtx, type Draw, type Pt } from '../p039-repro/recordingCtx'
import type { LayoutLink, LayoutNode } from '../types/layout'
import { createVizMapping } from '../vizMapping'
import { DIRECTION_MARKER_ALPHA, drawBaseGraph } from './baseGraph'
import { withAlpha } from './color'
import { ARROW } from './linkArrowhead'

vi.mock('./nodePainter', () => ({ drawNodeShape: vi.fn() }))
vi.mock('./glowSprites', () => ({ drawGlowSprite: vi.fn() }))

const MAPPING = createVizMapping()
const LINE = MAPPING.link.color.default
const TX = MAPPING.fx.tx_spark.trail
/** The marker's own colour: the data token `--ds-data-direction`, not the line's (revised 2026-10-16). */
const DIR = withAlpha(MAPPING.node.palette.direction, 1)
/** Distances in the shipped size: tip from the centre of a 24 px circle; the minimum room between two rims. */
const TIP_AT = 12 + ARROW.tipGapPx
const ROOM = ARROW.minFreeGapPx

const node = (id: string, x: number, y: number): LayoutNode =>
  ({ id, __x: x, __y: y, type: 'person', status: 'active', viz_shape_key: 'circle', viz_size: { w: 24, h: 24 }, viz_color_key: 'person' }) as LayoutNode
const link = (source: string, target: string, over: Partial<LayoutLink> = {}): LayoutLink =>
  ({ source, target, __key: `${source}→${target}`, used: '10', trust_limit: '100', status: 'active', viz_width_key: 'mid', viz_alpha_key: 'hi', ...over }) as LayoutLink

type Opts = Partial<Parameters<typeof drawBaseGraph>[1]>

function draw(nodes: LayoutNode[], links: LayoutLink[], opts: Opts = {}): Draw[] {
  const { ctx, draws } = recordingCtx()
  drawBaseGraph(ctx, { w: 800, h: 600, nodes, links, mapping: MAPPING, selectedNodeId: null, activeEdges: new Map(), cameraZoom: 1, quality: 'low', ...opts })
  return draws
}

type Marker = { tip: Pt; base: [Pt, Pt]; style: string; index: number }

/** Fills of a closed path of exactly three points, in drawing order. The first point of the path is the tip. */
function markers(draws: Draw[]): Marker[] {
  const out: Marker[] = []
  draws.forEach((d, index) => {
    if (d.kind !== 'fill') return
    const ops = d.path.map((p) => p.op).join(',')
    if (ops !== 'moveTo,lineTo,lineTo,close') return
    const [tip, l, r] = d.path.slice(0, 3).map((p) => p.at[0]!) as [Pt, Pt, Pt]
    out.push({ tip, base: [l, r], style: d.fillStyle, index })
  })
  return out
}

const bodies = (draws: Draw[]) => draws.map((d, index) => ({ d, index })).filter(({ d }) => d.kind === 'stroke' && d.path.map((p) => p.op).join(',') === 'moveTo,lineTo')
const at = (n: LayoutNode): Pt => ({ x: n.__x, y: n.__y })
const shape = (m: Marker, origin: Pt) => [m.tip, ...m.base].map((p) => [Math.round((p.x - origin.x) * 1e6) / 1e6, Math.round((p.y - origin.y) * 1e6) / 1e6])

describe('drawBaseGraph - the direction marker of a trust line', () => {
  const creditor = node('creditor', 100, 300)
  const debtor = node('debtor', 300, 300)
  const both = [creditor, debtor]

  it('one-way line creditor -> debtor: ONE marker, at the debtor (target), none at the creditor (source)', () => {
    const m = markers(draw(both, [link('creditor', 'debtor')]))
    expect(m, 'markers of a one-way line').toHaveLength(1)
    expect(dist(m[0]!.tip, at(debtor)), 'the tip is 2 px before the rim of the debtor (radius 12)').toBeCloseTo(TIP_AT, 6)
    expect(dist(m[0]!.tip, at(creditor)), 'and far from the creditor').toBeCloseTo(200 - TIP_AT, 6)
    expect(m.filter((x) => dist(x.tip, at(creditor)) < 100), 'no marker in the creditor half').toHaveLength(0)
  })

  it('the SAME two nodes with source and target swapped: the marker is at the other node - it follows the wire direction, not the picture', () => {
    const m = markers(draw(both, [link('debtor', 'creditor')]))
    expect(m).toHaveLength(1)
    expect(dist(m[0]!.tip, at(creditor)), 'now `creditor` is the target of the line').toBeCloseTo(TIP_AT, 6)
  })

  it('mutual pair with different debts: two markers, one at each end, each drawn for ITS line (in the order of the lines)', () => {
    const m = markers(draw(both, [link('creditor', 'debtor', { used: '50' }), link('debtor', 'creditor', { used: '5', viz_alpha_key: 'bg' })]))
    expect(m, 'markers of a mutual pair').toHaveLength(2)
    expect(dist(m[0]!.tip, at(debtor)), 'the first line creditor -> debtor: at `debtor`').toBeCloseTo(TIP_AT, 6)
    expect(dist(m[1]!.tip, at(creditor)), 'the second line debtor -> creditor: at `creditor`').toBeCloseTo(TIP_AT, 6)
    // Both on the common axis y = 300, pointing at their own target.
    for (const x of m) expect((x.base[0].y + x.base[1].y) / 2).toBeCloseTo(300, 6)
    expect(m[0]!.style, 'the two markers have the same solid colour although the lines have different alpha').toBe(m[1]!.style)
  })

  it('a line with `used` = 0 has a marker, and it is the same triangle in the same colour as that of a line with debt', () => {
    const none = markers(draw(both, [link('creditor', 'debtor', { used: '0', viz_alpha_key: 'bg' })]))
    const debt = markers(draw(both, [link('creditor', 'debtor', { used: '80', viz_alpha_key: 'hi' })]))
    expect(none, 'a marker at used = 0').toHaveLength(1)
    expect(shape(none[0]!, at(debtor))).toEqual(shape(debt[0]!, at(debtor)))
    expect(none[0]!.style).toBe(debt[0]!.style)
  })

  it('the marker is solid in the direction colour of the palette: it does not inherit the alpha of a faint line, nor the colour the snapshot gives the line body', () => {
    expect(DIRECTION_MARKER_ALPHA, 'opaque').toBe(1)
    expect(MAPPING.node.palette.direction, 'not the faint colour of the line').not.toBe(LINE)
    for (const key of ['bg', 'muted', 'active', 'hi']) {
      const m = markers(draw(both, [link('creditor', 'debtor', { viz_alpha_key: key })]))
      expect(m[0]!.style, `alpha key ${key}`).toBe(DIR)
    }
    const draws = draw(both, [link('creditor', 'debtor', { viz_color_key: 'warm' })], { palette: { warm: { color: '#aa5500' } } })
    expect(markers(draws)[0]!.style, 'the marker keeps its own colour').toBe(DIR)
    expect(bodies(draws)[0]!.d.strokeStyle, 'the body takes the palette colour, at the alpha of the line').toBe(withAlpha('#aa5500', MAPPING.link.alpha.hi!))
    // The palette is the resolved object: a new direction colour there is the colour of the next pass.
    const other = { ...MAPPING, node: { palette: { ...MAPPING.node.palette, direction: '#123456' } } }
    const { ctx, draws: d2 } = recordingCtx()
    drawBaseGraph(ctx, { w: 800, h: 600, nodes: both, links: [link('creditor', 'debtor')], mapping: other, selectedNodeId: null, activeEdges: new Map(), cameraZoom: 1 })
    expect(markers(d2)[0]!.style).toBe(withAlpha('#123456', 1))
  })

  it('the body of the line is drawn as before: its own open path and stroke, rim to rim; the marker is a separate closed path and fill, after every body', () => {
    const draws = draw(both, [link('creditor', 'debtor'), link('debtor', 'creditor')])
    const b = bodies(draws)
    expect(b, 'two bodies, one per line - the pair is not merged').toHaveLength(2)
    expect(b[0]!.d.path.map((p) => p.at[0]!.x), 'the body runs from the rim of the source to the rim of the target').toEqual([112, 288])
    expect(b[0]!.d.strokeStyle).toBe(withAlpha(LINE, MAPPING.link.alpha.hi!))
    expect(b[0]!.d.lineWidth).toBe(MAPPING.link.width_px.mid)
    const m = markers(draws)
    expect(draws.filter((d) => d.kind === 'fill'), 'one fill per marker: no batching').toHaveLength(2)
    expect(Math.min(...m.map((x) => x.index)), 'no body is stroked over a marker').toBeGreaterThan(Math.max(...b.map((x) => x.index)))
    expect(draws.filter((d) => d.kind === 'stroke' && d.path.some((p) => p.op === 'close')), 'no stroke of a closed path: the shaft and the head are never one path').toHaveLength(0)
  })

  it('the marker has one size on screen at zoom 0.5, 1 and 2', () => {
    const far = node('far', 1000, 300)
    for (const zoom of [0.5, 1, 2]) {
      const [m] = markers(draw([creditor, far], [link('creditor', 'far')], { cameraZoom: zoom }))
      const mid = { x: (m!.base[0].x + m!.base[1].x) / 2, y: (m!.base[0].y + m!.base[1].y) / 2 }
      expect(dist(m!.tip, mid) * zoom, `length at zoom ${zoom}`).toBeCloseTo(ARROW.lengthPx, 6)
      expect(dist(m!.base[0], m!.base[1]) * zoom, `width at zoom ${zoom}`).toBeCloseTo(ARROW.widthPx, 6)
      expect((dist(m!.tip, at(far)) - 12 / zoom) * zoom, `tip to the rim at zoom ${zoom}`).toBeCloseTo(2, 6)
    }
  })

  it('less than the minimum room between the rims: the line is drawn, the marker is not; at the minimum it is', () => {
    const near = node('near', 124 + ROOM - 1, 300) // one px short
    const draws = draw([creditor, near], [link('creditor', 'near'), link('near', 'creditor')])
    expect(bodies(draws), 'the bodies of the pair').toHaveLength(2)
    expect(markers(draws), 'no marker without room').toHaveLength(0)
    expect(markers(draw([creditor, node('ok', 124 + ROOM, 300)], [link('creditor', 'ok')])), 'a marker at exactly the minimum').toHaveLength(1)
  })

  it('`focus` LOD (a node is being dragged): markers follow the lines that remain, and only them', () => {
    const third = node('third', 300, 500)
    const links = [link('creditor', 'debtor'), link('debtor', 'third'), link('third', 'creditor')]
    const full = markers(draw([...both, third], links))
    expect(full, 'full LOD: every line').toHaveLength(3)
    const draws = draw([...both, third], links, { linkLod: 'focus', dragMode: true, selectedNodeId: 'third' })
    expect(bodies(draws), 'focus LOD: the two lines of the dragged node').toHaveLength(2)
    const m = markers(draws)
    expect(m).toHaveLength(2)
    expect(dist(m[0]!.tip, at(third)), 'debtor -> third: at `third`').toBeCloseTo(TIP_AT, 6)
    expect(dist(m[1]!.tip, at(creditor)), 'third -> creditor: at `creditor`').toBeCloseTo(TIP_AT, 6)
  })

  it('`full` LOD never drops a marker for the number of lines or for the zoom while there is room', () => {
    const ring = Array.from({ length: 120 }, (_, i) => node(`n${i}`, 4000 * Math.cos((i / 120) * 2 * Math.PI), 4000 * Math.sin((i / 120) * 2 * Math.PI)))
    const links = ring.map((n, i) => link(n.id, ring[(i + 1) % ring.length]!.id))
    for (const zoom of [0.2, 1, 3]) expect(markers(draw(ring, links, { cameraZoom: zoom })), `120 lines at zoom ${zoom}`).toHaveLength(120)
  })

  it('a selected participant (focus overlay): its lines are stroked again in their OWN colour, the markers stay as they were - one per line, at the target', () => {
    const plain = markers(draw(both, [link('creditor', 'debtor')]))
    const draws = draw(both, [link('creditor', 'debtor')], { selectedNodeId: 'creditor' })
    expect(bodies(draws), 'the body and the overlay stroke over it').toHaveLength(2)
    expect(bodies(draws)[1]!.d.strokeStyle, 'the overlay keeps the colour of the line').toBe(withAlpha(LINE, 0.96))
    const m = markers(draws)
    expect(m.map((x) => [x.tip, x.style])).toEqual(plain.map((x) => [x.tip, x.style]))
  })

  it('an active line (clearing, scripted highlight): the marker is drawn again in the highlight colour at the SAME target end, and never at the source', () => {
    const l = link('creditor', 'debtor')
    const draws = draw(both, [l], { activeEdges: new Map([[l.__key, 1]]) })
    const m = markers(draws)
    expect(m, 'the marker of the line and the marker of its highlight').toHaveLength(2)
    expect(m[0]!.style).toBe(DIR)
    expect(m[1]!.style, 'the highlight marker is the colour of the highlight stroke').toBe(withAlpha(TX, 1))
    expect(m[1]!.tip, 'at the same place').toEqual(m[0]!.tip)
    expect(m[1]!.base).toEqual(m[0]!.base)
    const overlay = bodies(draws)[1]!
    expect(overlay.d.strokeStyle, 'the highlight stroke').toBe(withAlpha(TX, MAPPING.link.alpha.hi! * 4 > 1 ? 1 : MAPPING.link.alpha.hi! * 4))
    expect(m[1]!.index, 'the highlight marker is drawn after the highlight stroke, so the stroke does not cover it').toBeGreaterThan(overlay.index)
    expect(m.filter((x) => dist(x.tip, at(creditor)) < 100), 'nothing at the source').toHaveLength(0)
  })

  it('a fading highlight: its marker fades with it and is gone when the stroke is, the marker of the line itself stays opaque', () => {
    const l = link('creditor', 'debtor')
    const half = markers(draw(both, [l], { activeEdges: new Map([[l.__key, 0.5]]) }))
    expect(half.map((x) => x.style)).toEqual([DIR, withAlpha(TX, 0.5)])
    const gone = markers(draw(both, [l], { activeEdges: new Map([[l.__key, 0.002]]) }))
    expect(gone.map((x) => x.style), 'below the threshold of the highlight stroke: no highlight marker').toEqual([DIR])
  })

  it('an active line without room for a marker gets none in the highlight either', () => {
    const near = node('near', 124 + ROOM - 1, 300)
    const l = link('creditor', 'near')
    const draws = draw([creditor, near], [l], { activeEdges: new Map([[l.__key, 1]]) })
    expect(bodies(draws), 'body and highlight stroke').toHaveLength(2)
    expect(markers(draws)).toHaveLength(0)
  })

  it('a line to a node that is not in the scene: no throw and no marker; the other lines keep theirs', () => {
    const m = markers(draw(both, [link('creditor', 'ghost'), link('ghost', 'debtor'), link('creditor', 'debtor')]))
    expect(m).toHaveLength(1)
  })
})
