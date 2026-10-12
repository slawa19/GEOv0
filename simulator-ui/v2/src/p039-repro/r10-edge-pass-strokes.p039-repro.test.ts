/**
 * 039 T3901 - R10 (batching of the edge pass), RENDERER tier. MEASUREMENT ONLY since 2026-10-12.
 *
 * WITHDRAWN AS A REPRODUCER (spec 039, P-8): the off-screen experiment of the browser tier showed that grouping the
 * lines into one path - by style group, or by run with the order kept - changes the picture on today's fixture, so
 * batching is not done and "strokes <= style groups" is not a budget. The red assertion (348 <= 12) was removed; what
 * stays are the counts a later decision (a dirty key) starts from. No slice flips anything here: nothing is `it.fails`.
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
import { VIZ_MAPPING } from '../vizMapping'
import { fixture100, measured } from './p039'
import { recordingCtx, segmentsOf, type Draw } from './recordingCtx'

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

  it('measurement: strokes, lines drawn, style groups, runs of one style in snapshot order', () => {
    measured('R10 edge pass', {
      links: links.length, strokes: strokes.length, segments: segments.length, otherDraws: draws.length - strokes.length,
      styleGroups: groups.size, groups: [...groups].sort(), runsInSnapshotOrder: runs,
    })
    expect(links).toHaveLength(348)
    expect(segments, 'counter-check: every trust line of the fixture is drawn').toHaveLength(348)
    expect(strokes, 'today: one stroke per trust line').toHaveLength(348)
    expect(groups.size, 'style groups of the fixture: 3 widths x 4 alphas, one colour').toBe(12)
    expect(runs, 'runs of consecutive same-style lines in snapshot order').toBe(175)
  })

  it('counter-check (P-8): a selected node and an active edge ADD strokes of the highlight pass - they are not batched away', () => {
    const hub = links[0]!.source
    const incident = links.filter((l) => l.source === hub || l.target === hub).length
    const focused = edgePass({ selectedNodeId: hub }).filter((d) => d.kind === 'stroke')
    const active = edgePass({ activeEdges: new Map([[links[5]!.__key, 1]]) }).filter((d) => d.kind === 'stroke')
    measured('R10 highlight pass', {
      incidentToSelected: incident, strokesWithSelection: focused.length, addedBySelection: focused.length - strokes.length,
      strokesWithOneActiveEdge: active.length, addedByActiveEdge: active.length - strokes.length,
    })
    expect(incident).toBeGreaterThan(0)
    expect(focused.flatMap(segmentsOf).length - segments.length, 'one more line per trust line of the selected node').toBe(incident)
    expect(active.flatMap(segmentsOf).length - segments.length, 'one more line for the active edge').toBe(1)
    const highlight = active.filter((d) => d.lineWidth === VIZ_MAPPING.link.width_px.highlight)
    expect(highlight, 'the active edge is drawn in the highlight width').toHaveLength(1)
  })
})
