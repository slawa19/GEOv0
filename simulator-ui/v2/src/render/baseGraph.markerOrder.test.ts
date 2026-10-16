/**
 * 039 slice B, PR 2, fix-delta of 2026-10-16 (external review, finding 1): nothing the base canvas paints LATER may
 * cover a direction marker - not the ring of a hub that is the debtor, not the highlight stroke of the other line of
 * a mutual pair. The markers are drawn last; a marker that a THIRD node lies over is drawn before the nodes, so that
 * node covers it as it covers the line.
 *
 * Observed: the order of drawing calls on a recording context, with the REAL node painter (only the glow sprites,
 * which need a canvas, are mocked). "A node's paint" = every draw whose path has an arc or a rect centred on that
 * node. Limits: order, not pixels (pixels of the hub case: `e2e/qa/direction.qa.ts`).
 */
import { describe, expect, it, vi } from 'vitest'

import { dist, recordingCtx, type Draw, type Pt } from '../p039-repro/recordingCtx'
import type { LayoutLink, LayoutNode } from '../types/layout'
import { createVizMapping } from '../vizMapping'
import { drawBaseGraph } from './baseGraph'
import { withAlpha } from './color'
import { ARROW } from './linkArrowhead'

vi.mock('./glowSprites', () => ({ drawGlowSprite: vi.fn() }))

const MAPPING = createVizMapping()
const DIR = withAlpha(MAPPING.node.palette.direction, 1)
const TX = MAPPING.fx.tx_spark.trail

const node = (id: string, x: number, y: number, over: Partial<LayoutNode> = {}): LayoutNode =>
  ({ id, __x: x, __y: y, type: 'person', status: 'active', net_sign: 0, viz_shape_key: 'circle', viz_size: { w: 24, h: 24 }, ...over }) as LayoutNode
const link = (source: string, target: string, over: Partial<LayoutLink> = {}): LayoutLink =>
  ({ source, target, __key: `${source}→${target}`, used: '10', trust_limit: '100', status: 'active', viz_width_key: 'mid', viz_alpha_key: 'hi', ...over }) as LayoutLink

function draw(nodes: LayoutNode[], links: LayoutLink[], opts: Partial<Parameters<typeof drawBaseGraph>[1]> = {}): Draw[] {
  const { ctx, draws } = recordingCtx()
  drawBaseGraph(ctx, { w: 800, h: 600, nodes, links, mapping: MAPPING, selectedNodeId: null, activeEdges: new Map(), cameraZoom: 1, quality: 'low', ...opts })
  return draws
}

const isMarker = (d: Draw) => d.kind === 'fill' && d.path.map((p) => p.op).join(',') === 'moveTo,lineTo,lineTo,close'
const markersAt = (draws: Draw[], n: LayoutNode) => draws.map((d, i) => ({ d, i })).filter(({ d }) => isMarker(d) && dist(d.path[0]!.at[0]!, at(n)) < 30)
const at = (n: LayoutNode): Pt => ({ x: n.__x, y: n.__y })
/** Every draw of the node's own paint: a path with an arc or a rect centred on it. */
const paintOf = (draws: Draw[], n: LayoutNode) => draws.map((d, i) => ({ d, i })).filter(({ d }) => d.path.some((p) => (p.op === 'arc' && dist(p.at[0]!, at(n)) < 1e-6) || (p.op === 'rect' && dist({ x: (p.at[0]!.x + p.at[1]!.x) / 2, y: (p.at[0]!.y + p.at[1]!.y) / 2 }, at(n)) < 1e-6)))

describe('direction markers are painted after everything that could cover them', () => {
  const creditor = node('creditor', 100, 300)

  it('a HUB as the debtor: the marker is drawn after the node and its ring (the ring used to cut its neck)', () => {
    const hub = node('hub', 300, 300, { type: 'hub' })
    const draws = draw([creditor, hub], [link('creditor', 'hub')])
    const paint = paintOf(draws, hub)
    const ring = paint.filter(({ d }) => d.kind === 'stroke' && d.strokeStyle === MAPPING.node.palette.hub)
    expect(ring, 'counter-check: the hub ring is painted (the real painter ran)').toHaveLength(1)
    const m = markersAt(draws, hub)
    expect(m, 'one marker at the hub').toHaveLength(1)
    expect(m[0]!.i, 'after the last draw of the hub, its ring included').toBeGreaterThan(Math.max(...paint.map((p) => p.i)))
    expect(m[0]!.d.fillStyle).toBe(DIR)
  })

  it('an ordinary debtor too: its marker comes after the node is painted', () => {
    const debtor = node('debtor', 300, 300, { net_sign: -1 })
    const draws = draw([creditor, debtor], [link('creditor', 'debtor')])
    expect(paintOf(draws, debtor).length, 'counter-check: the node is painted').toBeGreaterThan(0)
    expect(markersAt(draws, debtor)[0]!.i).toBeGreaterThan(Math.max(...paintOf(draws, debtor).map((p) => p.i)))
  })

  it('a mutual pair with only A -> B highlighted: the marker of B -> A at A is drawn AFTER the highlight stroke crosses it, in its own colour', () => {
    const a = node('a', 100, 300)
    const b = node('b', 300, 300)
    const ab = link('a', 'b')
    const draws = draw([a, b], [ab, link('b', 'a')], { activeEdges: new Map([[ab.__key, 1]]) })
    const highlight = draws.map((d, i) => ({ d, i })).filter(({ d }) => d.kind === 'stroke' && d.strokeStyle === withAlpha(TX, 1))
    expect(highlight, 'counter-check: the highlight stroke of A -> B is drawn').toHaveLength(1)
    const atA = markersAt(draws, a)
    expect(atA, 'the marker of B -> A, and no highlight marker at A').toHaveLength(1)
    expect(atA[0]!.d.fillStyle, 'in the marker colour - B -> A is not highlighted').toBe(DIR)
    expect(atA[0]!.i, 'after the highlight stroke').toBeGreaterThan(highlight[0]!.i)
    const atB = markersAt(draws, b)
    expect(atB.map(({ d }) => d.fillStyle), 'at B: the marker of A -> B and its highlight over it').toEqual([DIR, withAlpha(TX, 1)])
  })

  it('a THIRD node lying over the marker covers it, as it covers the line: that marker is drawn before the nodes', () => {
    const debtor = node('debtor', 300, 300)
    // A small node centred on the middle of the marker of creditor -> debtor.
    const midX = 300 - 12 - ARROW.tipGapPx - ARROW.lengthPx / 2
    const third = node('third', midX, 300, { viz_size: { w: 8, h: 8 } })
    const draws = draw([creditor, debtor, third], [link('creditor', 'debtor')])
    const m = markersAt(draws, debtor)
    expect(m).toHaveLength(1)
    expect(m[0]!.i, 'before the third node is painted').toBeLessThan(Math.min(...paintOf(draws, third).map((p) => p.i)))
    // Moved away, the same node covers nothing: the marker is last again.
    const away = node('third', 300, 100, { viz_size: { w: 8, h: 8 } })
    const d2 = draw([creditor, debtor, away], [link('creditor', 'debtor')])
    expect(markersAt(d2, debtor)[0]!.i).toBeGreaterThan(Math.max(...paintOf(d2, away).map((p) => p.i), ...paintOf(d2, debtor).map((p) => p.i)))
  })

  it('a node being dragged is not painted on the canvas (the DOM preview is): it covers no marker', () => {
    const debtor = node('debtor', 300, 300)
    const midX = 300 - 12 - ARROW.tipGapPx - ARROW.lengthPx / 2
    const third = node('third', midX, 300, { viz_size: { w: 8, h: 8 } })
    const draws = draw([creditor, debtor, third], [link('creditor', 'debtor')], { hiddenNodeId: 'third' })
    expect(paintOf(draws, third), 'the hidden node is not painted').toHaveLength(0)
    expect(markersAt(draws, debtor)[0]!.i).toBeGreaterThan(Math.max(...paintOf(draws, debtor).map((p) => p.i)))
  })

  it('an organisation (rectangle) as the third node covers a marker under its box', () => {
    const debtor = node('debtor', 300, 300)
    const midX = 300 - 12 - ARROW.tipGapPx - ARROW.lengthPx / 2
    const shop = node('shop', midX, 300 + 6, { viz_shape_key: 'rounded-rect', viz_size: { w: 10, h: 10 } })
    const draws = draw([creditor, debtor, shop], [link('creditor', 'debtor')])
    const paint = paintOf(draws, shop)
    expect(paint.length, 'counter-check: the organisation is painted').toBeGreaterThan(0)
    expect(markersAt(draws, debtor)[0]!.i).toBeLessThan(Math.min(...paint.map((p) => p.i)))
  })
})
