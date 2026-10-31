import { describe, expect, it } from 'vitest'

import type { LayoutNode } from '../../types/layout'
import { createVizMapping } from '../../vizMapping'
import { getLinkTermination } from '../linkGeometry'

import { renderFxFrame } from './renderFrame'
import type { FxState } from './state'

/**
 * MM1-M2: the clearing edge pulse runs from the BORDER of one node to the border of the other - the same ends as the
 * payment beam (`getLinkTermination`) - not from centre to centre over the bodies of the nodes (the FX canvas is above
 * the base canvas, so a centre-to-centre stroke is drawn ON the node). Every phase of the pulse is judged: the orange
 * strokes, the blue crossfade seed in its tail, and the blue afterglow.
 *
 * What it does NOT see: pixels (the visual baseline `scene-E-clearing` does not hold a pulse either: its button is an
 * e2e stub that spawns no FX - see `e2e/scenes.spec.ts`), and node bursts, which are drawn at the node on purpose.
 */

type Seg = { from: [number, number]; to: [number, number] }

function recordingCtx() {
  const segs: Seg[] = []
  let at: [number, number] | null = null
  const ctx = {
    globalCompositeOperation: 'source-over', globalAlpha: 1, strokeStyle: '#000', fillStyle: '#000', lineWidth: 1, lineCap: 'butt', lineJoin: 'miter',
    save: () => undefined,
    restore: () => undefined,
    beginPath: () => { at = null },
    moveTo: (x: number, y: number) => { at = [x, y] },
    lineTo: (x: number, y: number) => { if (at) segs.push({ from: at, to: [x, y] }); at = [x, y] },
    stroke: () => undefined,
  }
  return { ctx: ctx as unknown as CanvasRenderingContext2D, segs }
}

const A: LayoutNode = { id: 'A', __x: 0, __y: 0, viz_size: { w: 20, h: 20 } } as LayoutNode
// A rounded-rect node, so the box branch of the termination is judged too.
const B: LayoutNode = { id: 'B', __x: 100, __y: 30, viz_size: { w: 40, h: 20 }, viz_shape_key: 'rounded-rect' } as LayoutNode

describe('MM1-M2 renderFxFrame: the clearing edge pulse ends at the node borders', () => {
  const duration = 1000
  // Orange phase; orange + crossfade seed (t0 > 0.55); afterglow (after `durationMs`).
  it.each([
    ['orange', 300],
    ['orange + crossfade seed', 700],
    ['afterglow', 1100],
  ])('%s: every stroke of the pulse runs border to border', (_phase, age) => {
    const { ctx, segs } = recordingCtx()
    const fxState: FxState = {
      sparks: [],
      edgePulses: [{ key: 'A>B', from: 'A', to: 'B', startedAtMs: 0, durationMs: duration, color: '#f97316', thickness: 2, seed: 1 }],
      nodeBursts: [],
    }
    const pos = new Map<string, LayoutNode>([['A', A], ['B', B]])
    renderFxFrame({ nowMs: age, ctx, pos, w: 400, h: 300, mapping: createVizMapping(), fxState, isTestMode: true, cameraZoom: 1, quality: 'high' })

    const s = { ...getLinkTermination(A, B, 1) }
    const e = { ...getLinkTermination(B, A, 1) }
    // Counter-check: the ends are NOT the centres, so equality below cannot hold for a centre-to-centre stroke.
    expect(Math.hypot(s.x - A.__x, s.y - A.__y)).toBeGreaterThan(5)
    expect(Math.hypot(e.x - B.__x, e.y - B.__y)).toBeGreaterThan(5)

    expect(segs.length, 'the pulse drew strokes in this phase').toBeGreaterThan(0)
    for (const seg of segs) {
      expect(seg.from[0]).toBeCloseTo(s.x, 6)
      expect(seg.from[1]).toBeCloseTo(s.y, 6)
      expect(seg.to[0]).toBeCloseTo(e.x, 6)
      expect(seg.to[1]).toBeCloseTo(e.y, 6)
    }
  })
})
