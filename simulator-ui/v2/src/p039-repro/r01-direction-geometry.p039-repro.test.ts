/**
 * 039 T3901 - R1 (direction of a trust line), GEOMETRY tier. Pixels: `e2e/qa/p039-repro/r01-direction-pixels.p039-repro.ts`.
 *
 * Contract (spec 039, P-1): a trust line `source -> target` points creditor -> debtor; the arrowhead is at the debtor.
 * Corrected form (P-2): a one-way line and a mutual pair are judged SEPARATELY; the probe does not take node paint.
 *
 * What is observed: what `drawBaseGraph` draws in its edge pass (node painting is mocked out, so nothing of a node is
 * in the record). "A marker at an end" = anything drawn within `PROBE_PX` of the point where the line meets that node
 * which is NOT the line itself: a path point off the axis of the line, an arc / rect, an image.
 *
 * LIMITS. (1) It sees a marker drawn by `drawBaseGraph` only; a marker drawn by another function of the render loop
 * would leave it red on a correct product (the spec names `baseGraph.ts`). (2) It judges where something is drawn, not
 * what it looks like - shape, size and colour are the pixel tier's and the owner's. (3) Zoom 1 only.
 */
import { describe, expect, it, vi } from 'vitest'

import { drawBaseGraph } from '../render/baseGraph'
import { getLinkTermination } from '../render/linkGeometry'
import type { LayoutLink, LayoutNode } from '../types/layout'
import { VIZ_MAPPING } from '../vizMapping'
import { measured, red } from './p039'
import { allPoints, dist, recordingCtx, segmentsOf, type Draw, type Pt } from './recordingCtx'

vi.mock('../render/nodePainter', () => ({ drawNodeShape: vi.fn() }))
vi.mock('../render/glowSprites', () => ({ drawGlowSprite: vi.fn() }))

/** Radius of the probe round an end of a line. The two ends of the 200 px line below are 176 px apart: the probes are disjoint. */
const PROBE_PX = 16
/** A path point this far from the axis of the line is not the line (the line is drawn on its axis exactly). */
const OFF_AXIS_PX = 0.75

const node = (id: string, x: number, y: number): LayoutNode => ({
  id, __x: x, __y: y, type: 'person', status: 'active', viz_shape_key: 'circle', viz_size: { w: 24, h: 24 }, viz_color_key: 'person',
})
const link = (source: string, target: string): LayoutLink =>
  ({ source, target, __key: `${source}→${target}`, used: '10', trust_limit: '100', status: 'active', viz_width_key: 'mid', viz_alpha_key: 'hi' }) as LayoutLink

function drawScene(nodes: LayoutNode[], links: LayoutLink[]): Draw[] {
  const { ctx, draws } = recordingCtx()
  drawBaseGraph(ctx, { w: 800, h: 600, nodes, links, mapping: VIZ_MAPPING, selectedNodeId: null, activeEdges: new Map(), cameraZoom: 1, quality: 'low' })
  return draws
}

function offAxis(p: Pt, a: Pt, b: Pt): number {
  const dx = b.x - a.x
  const dy = b.y - a.y
  return Math.abs((p.x - a.x) * dy - (p.y - a.y) * dx) / Math.hypot(dx, dy)
}

/** Evidence of a marker at the end where the line from `far` meets `near`. */
function markerEvidence(draws: Draw[], near: LayoutNode, far: LayoutNode): number {
  const end = { ...getLinkTermination(near, far, 1) }
  const a = { x: near.__x, y: near.__y }
  const b = { x: far.__x, y: far.__y }
  let n = 0
  for (const d of draws) {
    if (d.kind !== 'stroke' && d.kind !== 'fill') { if (allPoints(d).some((p) => dist(p, end) <= PROBE_PX)) n += 1; continue }
    for (const op of d.path) {
      const close = op.at.filter((p) => dist(p, end) <= PROBE_PX)
      if (close.length === 0) continue
      if (op.op === 'arc' || op.op === 'rect' || op.op === 'curve') n += 1
      else n += close.filter((p) => offAxis(p, a, b) > OFF_AXIS_PX).length
    }
  }
  return n
}

describe('039 R1 - a trust line shows its direction (geometry of the edge pass)', () => {
  const creditor = node('creditor', 100, 300)
  const debtor = node('debtor', 300, 300)

  it('counter-check: the recorder sees the edge pass - one line creditor -> debtor is drawn between the two node rims', () => {
    const draws = drawScene([creditor, debtor], [link('creditor', 'debtor')])
    const segs = draws.flatMap(segmentsOf)
    expect(segs).toHaveLength(1)
    expect(segs[0]![0].x).toBeCloseTo(112, 5) // the rim of a 24 px circle at x = 100
    expect(segs[0]![1].x).toBeCloseTo(288, 5)
  })

  red('one-way line creditor -> debtor: a marker is drawn at the debtor end (today: none anywhere)', () => {
    const draws = drawScene([creditor, debtor], [link('creditor', 'debtor')])
    const atDebtor = markerEvidence(draws, debtor, creditor)
    const atCreditor = markerEvidence(draws, creditor, debtor)
    measured('R1 geometry one-way', { atDebtor, atCreditor, draws: draws.length })
    expect(atDebtor, 'marker evidence at the debtor end of creditor -> debtor').toBeGreaterThan(0)
    // Meaningful only once the line above is green: with no marker anywhere this is 0 by default.
    expect(atCreditor, 'no marker at the creditor end of a ONE-WAY line').toBe(0)
  })

  red('mutual pair a -> b and b -> a: each is the debtor of one line, so a marker is drawn at BOTH ends (today: none)', () => {
    const draws = drawScene([creditor, debtor], [link('creditor', 'debtor'), link('debtor', 'creditor')])
    const ends = { atFirst: markerEvidence(draws, creditor, debtor), atSecond: markerEvidence(draws, debtor, creditor) }
    measured('R1 geometry mutual', { ...ends, segments: draws.flatMap(segmentsOf).length })
    expect(ends.atFirst, 'marker evidence at the first node of a mutual pair').toBeGreaterThan(0)
    expect(ends.atSecond, 'marker evidence at the second node of a mutual pair').toBeGreaterThan(0)
  })

  it('measurement: the two lines of a mutual pair are drawn on the same axis today (one covers the other)', () => {
    const segs = drawScene([creditor, debtor], [link('creditor', 'debtor'), link('debtor', 'creditor')]).flatMap(segmentsOf)
    const sameAxis = segs.length === 2 && dist(segs[0]![0], segs[1]![1]) < 1e-6 && dist(segs[0]![1], segs[1]![0]) < 1e-6
    measured('R1 mutual pair geometry', { segments: segs.length, sameAxis })
    expect(segs).toHaveLength(2)
  })
})
