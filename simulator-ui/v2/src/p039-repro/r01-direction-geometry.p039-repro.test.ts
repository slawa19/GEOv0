/**
 * 039 T3901 - R1 (direction of a trust line), GEOMETRY tier. Pixels: `e2e/qa/p039-repro/r01-direction-pixels.p039-repro.ts`.
 *
 * Contract (spec 039, P-1): a trust line `source -> target` points creditor -> debtor; the arrowhead is at the debtor.
 * Corrected form (P-2): a one-way line and a mutual pair are judged SEPARATELY; the probe does not take node paint.
 *
 * What is observed: what `drawBaseGraph` draws in its edge pass (node painting is mocked out, so nothing of a node is
 * in the record). "A marker at an end" = anything drawn within `PROBE_PX` of the point where the line meets that node
 * which is NOT the line itself: a path point off the axis of the line, an arc / rect, an image.
 * (Since the review of 2026-10-12 the probe is the debtor's HALF of the line, not a 16 px disc: no marker size is assumed.)
 *
 * LIMITS. (1) It sees a marker drawn by `drawBaseGraph` only; a marker drawn by another function of the render loop
 * would leave it red on a correct product (the spec names `baseGraph.ts`). (2) It judges where something is drawn, not
 * what it looks like - shape, size and colour are the pixel tier's and the owner's. (3) Zoom 1 only.
 *
 * SLICE B, PR 2 (2026-10-12) drew the marker in `baseGraph.ts`: the two reproducers are plain `it` now. Re-read
 * against decision 4 of the slice's design before the flip, and two things were corrected:
 *  - "in the debtor's half" accepted a marker in the MIDDLE of a line. Decision 4 puts the tip 2 px before the rim and
 *    the base 5 px farther: every piece of marker evidence must now lie within `AT_THE_END_PX` of the rim of that node,
 *    and the piece nearest to the node must be on the axis (the triangle POINTS at the debtor, it is not a bar or a
 *    triangle turned round). Shown by the two new synthetic controls.
 *  - a mutual pair was accepted with any evidence at both ends; it now needs the same amount at each (one arrowhead
 *    per direction), and the two bodies are counted.
 * What it still does not see: WHICH line a marker belongs to (two markers of a pair drawn for the wrong lines look the
 * same on the picture, too) - `render/baseGraph.direction.test.ts` holds the order; and zoom other than 1.
 */
import { describe, expect, it, vi } from 'vitest'

import { drawBaseGraph } from '../render/baseGraph'
import { ARROW } from '../render/linkArrowhead'
import type { LayoutLink, LayoutNode } from '../types/layout'
import { createVizMapping } from '../vizMapping'
import { measured } from './p039'
import { allPoints, dist, recordingCtx, segmentsOf, type Draw, type Pt } from './recordingCtx'

// 039 slice B (2026-10-12): the mapping is created per app, not a module singleton - only this line moved.
const VIZ_MAPPING = createVizMapping()

vi.mock('../render/nodePainter', () => ({ drawNodeShape: vi.fn() }))
vi.mock('../render/glowSprites', () => ({ drawGlowSprite: vi.fn() }))

/** A path point this far from the axis of the line is not the line (the line is drawn on its axis exactly). */
const OFF_AXIS_PX = 0.75
/** The shipped marker (decision 4 as revised 2026-10-16): tip 2 px before the rim, base one length farther - the whole marker within tip gap + length of the rim. One more for rounding. */
const AT_THE_END_PX = ARROW.tipGapPx + ARROW.lengthPx + 1
/** The stand-in nodes are 24 px circles. */
const RIM_PX = 12

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

/**
 * LINE BODIES: straight segments that lie on the axis between the two nodes and span more than half of it. A marker
 * (a chevron, a filled triangle, a dot), hatching of a frozen line or a ring round a hub is not a line body, whatever
 * primitive draws it - so the controls below do not forbid them.
 */
function lineBodies(draws: Draw[], a: LayoutNode, b: LayoutNode): Array<[Pt, Pt]> {
  const pa = { x: a.__x, y: a.__y }
  const pb = { x: b.__x, y: b.__y }
  const span = dist(pa, pb)
  return draws.filter((d) => d.kind === 'stroke').flatMap(segmentsOf)
    .filter(([p, q]) => offAxis(p, pa, pb) <= OFF_AXIS_PX && offAxis(q, pa, pb) <= OFF_AXIS_PX && dist(p, q) > span / 2)
}

/**
 * Evidence of a marker in the HALF of the line that is nearer to `near`: anything drawn there that is not a line
 * body - a path point off the axis, an arc / rect / curve, an image. No size or place of a marker is assumed beyond
 * "in the debtor's half"; the pixel tier looks at the rim.
 */
function markerEvidence(draws: Draw[], near: LayoutNode, far: LayoutNode): number {
  const a = { x: near.__x, y: near.__y }
  const b = { x: far.__x, y: far.__y }
  const inNearHalf = (p: Pt) => dist(p, a) < dist(p, b)
  let n = 0
  for (const d of draws) {
    if (d.kind !== 'stroke' && d.kind !== 'fill') { if (allPoints(d).some(inNearHalf)) n += 1; continue }
    for (const op of d.path) {
      const close = op.at.filter(inNearHalf)
      if (close.length === 0) continue
      if (op.op === 'arc' || op.op === 'rect' || op.op === 'curve') n += 1
      else n += close.filter((p) => offAxis(p, a, b) > OFF_AXIS_PX).length
    }
  }
  return n
}

/**
 * Is what is drawn near `near` an arrowhead AT THE END of the line and pointing at that node? Every path point of a
 * fill or stroke that is not a line body, in the half of `near`: all within `AT_THE_END_PX` of the rim, and the one
 * nearest to the node on the axis.
 */
function pointsAtTheEnd(draws: Draw[], near: LayoutNode, far: LayoutNode): { points: number; farthestFromRimPx: number; nearestIsOnAxis: boolean } {
  const a = { x: near.__x, y: near.__y }
  const b = { x: far.__x, y: far.__y }
  const bodySet = new Set(lineBodies(draws, near, far).flat())
  const pts = draws.filter((d) => d.kind === 'stroke' || d.kind === 'fill').flatMap((d) => d.path.flatMap((op) => op.at))
    .filter((p) => !bodySet.has(p) && dist(p, a) < dist(p, b))
  const nearest = [...pts].sort((p, q) => dist(p, a) - dist(q, a))[0]
  return {
    points: pts.length,
    farthestFromRimPx: pts.length ? Math.max(...pts.map((p) => dist(p, a) - RIM_PX)) : Number.NaN,
    nearestIsOnAxis: !!nearest && offAxis(nearest, a, b) <= OFF_AXIS_PX,
  }
}

describe('039 R1 - a trust line shows its direction (geometry of the edge pass)', () => {
  const creditor = node('creditor', 100, 300)
  const debtor = node('debtor', 300, 300)

  it('counter-check: the recorder sees the edge pass - the line creditor -> debtor has a body that starts at the creditor rim', () => {
    const bodies = lineBodies(drawScene([creditor, debtor], [link('creditor', 'debtor')]), creditor, debtor)
    measured('R1 line body', bodies)
    expect(bodies.length, 'line bodies between the two nodes').toBeGreaterThanOrEqual(1)
    const xs = bodies.flatMap(([p, q]) => [p.x, q.x])
    expect(Math.min(...xs), 'the body starts at the rim of the creditor (a 24 px circle at x = 100)').toBeCloseTo(112, 5)
    // The far end may stop short of the debtor rim (288) to leave room for an arrowhead: only "in the debtor half".
    expect(Math.max(...xs), 'and reaches into the debtor half').toBeGreaterThan(200)
  })

  it('counter-check of the probe: a chevron, a compact filled triangle and a dot at the debtor end are each seen as a marker there, and not at the creditor', () => {
    const body: Draw = { kind: 'stroke', strokeStyle: '', fillStyle: '', lineWidth: 1, globalAlpha: 1, path: [{ op: 'moveTo', at: [{ x: 112, y: 300 }] }, { op: 'lineTo', at: [{ x: 288, y: 300 }] }] }
    const shape = (kind: Draw['kind'], path: Draw['path']): Draw => ({ ...body, kind, path })
    const markers: Record<string, Draw> = {
      chevron: shape('stroke', [{ op: 'moveTo', at: [{ x: 280, y: 296 }] }, { op: 'lineTo', at: [{ x: 288, y: 300 }] }, { op: 'lineTo', at: [{ x: 280, y: 304 }] }]),
      compactTriangle: shape('fill', [{ op: 'moveTo', at: [{ x: 282, y: 298 }] }, { op: 'lineTo', at: [{ x: 288, y: 300 }] }, { op: 'lineTo', at: [{ x: 282, y: 302 }] }, { op: 'close', at: [] }]),
      dot: shape('fill', [{ op: 'arc', at: [{ x: 284, y: 300 }] }]),
    }
    for (const [name, m] of Object.entries(markers)) {
      expect(markerEvidence([body, m], debtor, creditor), `${name}: seen at the debtor`).toBeGreaterThan(0)
      expect(markerEvidence([body, m], creditor, debtor), `${name}: not seen at the creditor`).toBe(0)
      expect(lineBodies([body, m], creditor, debtor), `${name}: is not counted as a line body`).toHaveLength(1)
    }
    expect(markerEvidence([body], debtor, creditor), 'the line alone is not a marker').toBe(0)
  })

  it('counter-check of "at the end, pointing at the node": a triangle in the middle of the debtor half and a triangle turned round at the rim are both refused; the arrowhead of decision 4 is accepted', () => {
    const body: Draw = { kind: 'stroke', strokeStyle: '', fillStyle: '', lineWidth: 1, globalAlpha: 1, path: [{ op: 'moveTo', at: [{ x: 112, y: 300 }] }, { op: 'lineTo', at: [{ x: 288, y: 300 }] }] }
    const tri = (tip: number, base: number): Draw => ({ ...body, kind: 'fill', path: [{ op: 'moveTo', at: [{ x: tip, y: 300 }] }, { op: 'lineTo', at: [{ x: base, y: 298 }] }, { op: 'lineTo', at: [{ x: base, y: 302 }] }, { op: 'close', at: [] }] })
    const good = pointsAtTheEnd([body, tri(286, 281)], debtor, creditor)
    expect(good.farthestFromRimPx, 'decision 4: within 7 px of the rim').toBeLessThanOrEqual(AT_THE_END_PX)
    expect(good.nearestIsOnAxis).toBe(true)
    const midLine = pointsAtTheEnd([body, tri(240, 235)], debtor, creditor)
    expect(midLine.farthestFromRimPx, 'a marker in the middle of the half is far from the rim').toBeGreaterThan(AT_THE_END_PX)
    const turnedRound = pointsAtTheEnd([body, tri(281, 286)], debtor, creditor)
    expect(turnedRound.nearestIsOnAxis, 'a triangle whose BASE is at the node points away from it').toBe(false)
  })

  it('one-way line creditor -> debtor: a marker is drawn at the debtor end, at the rim and pointing at the debtor; none at the creditor', () => {
    const draws = drawScene([creditor, debtor], [link('creditor', 'debtor')])
    const atDebtor = markerEvidence(draws, debtor, creditor)
    const atCreditor = markerEvidence(draws, creditor, debtor)
    measured('R1 geometry one-way', { atDebtor, atCreditor, draws: draws.length })
    expect(atDebtor, 'marker evidence at the debtor end of creditor -> debtor').toBeGreaterThan(0)
    expect(atCreditor, 'no marker at the creditor end of a ONE-WAY line').toBe(0)
    const end = pointsAtTheEnd(draws, debtor, creditor)
    measured('R1 geometry one-way, at the end', end)
    expect(end.farthestFromRimPx, 'the whole marker is at the END of the line (within 8 px of the rim of the debtor)').toBeLessThanOrEqual(AT_THE_END_PX)
    expect(end.farthestFromRimPx, 'and outside the node').toBeGreaterThan(0)
    expect(end.nearestIsOnAxis, 'its point nearest to the debtor is on the axis: it points AT the debtor').toBe(true)
  })

  it('mutual pair a -> b and b -> a: each is the debtor of one line, so a marker is drawn at BOTH ends - the same at each, at the rim, pointing at its node', () => {
    const draws = drawScene([creditor, debtor], [link('creditor', 'debtor'), link('debtor', 'creditor')])
    const ends = { atFirst: markerEvidence(draws, creditor, debtor), atSecond: markerEvidence(draws, debtor, creditor) }
    measured('R1 geometry mutual', { ...ends, lineBodies: lineBodies(draws, creditor, debtor).length })
    expect(ends.atFirst, 'marker evidence at the first node of a mutual pair').toBeGreaterThan(0)
    expect(ends.atSecond, 'marker evidence at the second node of a mutual pair').toBeGreaterThan(0)
    expect(ends.atSecond, 'one arrowhead per direction: the same evidence at both ends').toBe(ends.atFirst)
    expect(lineBodies(draws, creditor, debtor), 'the pair keeps its two bodies, one over the other').toHaveLength(2)
    for (const [near, far] of [[creditor, debtor], [debtor, creditor]] as const) {
      const end = pointsAtTheEnd(draws, near, far)
      expect(end.farthestFromRimPx, `the marker at ${near.id} is at the end of the line`).toBeLessThanOrEqual(AT_THE_END_PX)
      expect(end.nearestIsOnAxis, `and points at ${near.id}`).toBe(true)
    }
  })

  it('measurement (dated 2026-10-12, base 90e17a4c; not an acceptance condition): the two lines of a mutual pair lie on one axis, one over the other', () => {
    const bodies = lineBodies(drawScene([creditor, debtor], [link('creditor', 'debtor'), link('debtor', 'creditor')]), creditor, debtor)
    measured('R1 mutual pair geometry', { lineBodies: bodies.length })
    expect(bodies.length, 'the pair is drawn at all').toBeGreaterThanOrEqual(1)
  })
})
