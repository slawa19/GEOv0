/**
 * 039 T3901 - R10, MEASUREMENT ONLY, dated 2026-10-12 on base 90e17a4c (100 strokes, 200 fills, 120 fillRects) -
 * logged, not an acceptance condition: what the node pass of the base canvas issues per frame, so that the budget of the
 * edge pass is not confused with a budget of the whole canvas (the arbiter's F2: "node strokes alone exceed 20").
 * The real node painter runs; only the glow sprites (off-screen canvases) are mocked out, so their `drawImage` calls
 * are NOT in these numbers - the browser tier counts those.
 * No slice flips anything here: a measurement, nothing is `it.fails` (R10 as a budget is withdrawn, spec 039 P-8).
 */
import { describe, expect, it, vi } from 'vitest'

import { drawNodeShape } from '../render/nodePainter'
import type { LayoutNode } from '../types/layout'
import { createVizMapping } from '../vizMapping'
import { fixture100, measured } from './p039'
import { recordingCtx } from './recordingCtx'

// 039 slice B (2026-10-12): the mapping is created per app, not a module singleton - only this line moved.
const VIZ_MAPPING = createVizMapping()

vi.mock('../render/glowSprites', () => ({ drawGlowSprite: vi.fn() }))

const snapshot = fixture100()

describe('039 R10 - measurement: the node pass (fixture: 100 nodes), per quality', () => {
  for (const quality of ['low', 'med', 'high'] as const) {
    it(`quality ${quality}: strokes and fills of 100 nodes`, () => {
      const { ctx, draws } = recordingCtx()
      snapshot.nodes.forEach((n, i) => drawNodeShape(ctx, { ...n, __x: 40 + (i % 10) * 80, __y: 40 + Math.floor(i / 10) * 60 } as LayoutNode, { mapping: VIZ_MAPPING, cameraZoom: 1, quality }))
      const by = (k: string) => draws.filter((d) => d.kind === k).length
      const row = { nodes: snapshot.nodes.length, strokes: by('stroke'), fills: by('fill'), fillRects: by('fillRect') }
      measured(`R10 node pass ${quality}`, row)
      // Logged, not asserted (a hub ring or a frozen outline adds strokes in a correct product). By contract: nodes are drawn.
      expect(draws.length, 'the node pass draws something for each of the 100 nodes').toBeGreaterThanOrEqual(snapshot.nodes.length)
    })
  }
})
