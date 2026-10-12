/**
 * 039 T3901 - R10 (batching of the edge pass), RENDERER tier. MEASUREMENT ONLY since 2026-10-12.
 *
 * WITHDRAWN AS A REPRODUCER (spec 039, P-8): the off-screen experiment of the browser tier showed that grouping the
 * lines into one path - by style group, or by run with the order kept - changes the picture on today's fixture, so
 * batching is not done and "strokes <= style groups" is not a budget. The red assertion (348 <= 12) was removed. The
 * counts are a DATED MEASUREMENT of the base (2026-10-12, 90e17a4c): they are logged, not asserted, so that slice B
 * (markers, hatching) is not held to 348 strokes. No slice flips anything here: nothing is `it.fails`.
 * Browser counts and the pixel experiment:
 * `e2e/qa/p039-repro/r10-strokes.p039-repro.ts`.
 *
 * Corrected form (P-2): the EDGE PASS is counted, apart from node outlines; the budget is derived from the number of
 * real style groups of the scene, not invented. Scene: the versioned 100-node fixture (348 trust lines).
 *
 * Node painting is mocked out here, so every recorded draw belongs to the edge pass. Node outlines are measured by
 * `r10-node-strokes.p039-repro.test.ts` (it needs the real painter, hence its own file).
 *
 * LIMITS. It counts calls, not cost and not pixels. Whether grouping keeps the PICTURE is not judged here at all (P-8:
 * crossing lines of different style, a mutual pair, overlapping translucent strokes) - that is the browser tier's.
 */
import { describe, expect, it, vi } from 'vitest'

import { drawBaseGraph } from '../render/baseGraph'
import type { LayoutLink, LayoutNode } from '../types/layout'
import { createVizMapping } from '../vizMapping'
import { fixture100, measured } from './p039'
import { recordingCtx, segmentsOf, type Draw } from './recordingCtx'

// 039 slice B (2026-10-12): the mapping is created per app, not a module singleton - only this line moved.
const VIZ_MAPPING = createVizMapping()

vi.mock('../render/nodePainter', () => ({ drawNodeShape: vi.fn() }))
vi.mock('../render/glowSprites', () => ({ drawGlowSprite: vi.fn() }))

const snapshot = fixture100()

/** Positions are irrelevant to a count of calls; a circle keeps every line non-degenerate. */
const nodes: LayoutNode[] = snapshot.nodes.map((n, i, all) => ({
  ...n, __x: 600 + 500 * Math.cos((2 * Math.PI * i) / all.length), __y: 400 + 350 * Math.sin((2 * Math.PI * i) / all.length),
}))
const links: LayoutLink[] = snapshot.links.map((l) => ({ ...l, __key: `${l.source}→${l.target}` }))

function edgePass(o: { selectedNodeId?: string | null; activeEdges?: Map<string, number> } = {}): Draw[] {
  const { ctx, draws } = recordingCtx()
  drawBaseGraph(ctx, {
    w: 1280, h: 720, nodes, links, mapping: VIZ_MAPPING, palette: snapshot.palette, selectedNodeId: o.selectedNodeId ?? null,
    activeEdges: o.activeEdges ?? new Map(), cameraZoom: 1, quality: 'low',
  })
  return draws
}

const styleOf = (d: Draw) => `${d.strokeStyle}|${d.lineWidth}`

describe('039 R10 - measurement: the edge pass of the base canvas, strokes and style groups (fixture: 100 nodes, 348 lines)', () => {
  const draws = edgePass()
  const strokes = draws.filter((d) => d.kind === 'stroke')
  const groups = new Set(strokes.map(styleOf))
  // Consecutive lines of one style, in the order the snapshot gives them: what a batcher that may NOT reorder would emit.
  let runs = 0
  strokes.forEach((d, i) => { if (i === 0 || styleOf(d) !== styleOf(strokes[i - 1]!)) runs += 1 })
  const segments = strokes.flatMap(segmentsOf)

  it('measurement (dated 2026-10-12, base 90e17a4c): strokes, style groups, runs of one style in snapshot order', () => {
    // LOGGED, NOT ASSERTED: 348 strokes, 12 style groups and 175 runs are what the base does. A direction marker, the
    // hatching of a frozen line or a different order of drawing may change every one of them in a correct product.
    measured('R10 edge pass', {
      links: links.length, strokes: strokes.length, segments: segments.length, otherDraws: draws.length - strokes.length,
      styleGroups: groups.size, groups: [...groups].sort(), runsInSnapshotOrder: runs,
    })
    // What stays true by contract: every trust line of the scene has a line body from one of its nodes to the other.
    const at = new Map(nodes.map((n) => [n.id, n]))
    const near = (p: { x: number; y: number }, id: string) => Math.hypot(p.x - at.get(id)!.__x, p.y - at.get(id)!.__y) <= 40 // within a node (the largest is 42 x 36)
    const drawn = links.filter((l) => segments.some(([p, q]) => (near(p, l.source) && near(q, l.target)) || (near(p, l.target) && near(q, l.source))))
    expect(links).toHaveLength(348)
    expect(drawn.length, 'trust lines of the fixture that have a line body between their two nodes').toBe(348)
  })

  it('measurement: a selected node and an active edge ADD drawing in the highlight pass', () => {
    const hub = links[0]!.source
    const incident = links.filter((l) => l.source === hub || l.target === hub).length
    const focused = edgePass({ selectedNodeId: hub }).filter((d) => d.kind === 'stroke')
    const active = edgePass({ activeEdges: new Map([[links[5]!.__key, 1]]) }).filter((d) => d.kind === 'stroke')
    measured('R10 highlight pass', {
      incidentToSelected: incident, strokesWithSelection: focused.length, addedBySelection: focused.length - strokes.length,
      strokesWithOneActiveEdge: active.length, addedByActiveEdge: active.length - strokes.length,
    })
    expect(incident).toBeGreaterThan(0)
    // By contract: a selection and an active edge ARE drawn (something is added). How many strokes, and their width, is logged.
    expect(focused.flatMap(segmentsOf).length, 'a selected node adds drawing to the edge pass').toBeGreaterThan(segments.length)
    expect(active.flatMap(segmentsOf).length, 'an active edge adds drawing to the edge pass').toBeGreaterThan(segments.length)
  })
})
