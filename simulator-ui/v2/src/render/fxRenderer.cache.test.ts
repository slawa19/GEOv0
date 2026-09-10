import { describe, expect, it, beforeEach, afterEach } from 'vitest'

import { __testing, resetFxRendererCaches } from './fxRenderer'
import { nodeOutlinePath2D } from './fxRenderer/outlineCache'
import { sizeForNode } from './nodeSizing'
import type { LayoutNode } from '../types/layout'

// JSDOM does not provide Path2D; fxRenderer caches Path2D instances.
// Provide a tiny stub sufficient for our warmup path.
class MockPath2D {
  arc() {
    // noop
  }
  rect() {
    // noop
  }
  addPath() {
    // noop
  }
}

function setPath2DGlobal(value: typeof Path2D | undefined): void {
  Object.defineProperty(globalThis, 'Path2D', {
    value,
    configurable: true,
    writable: true,
  })
}

function getPath2DGlobal(): typeof Path2D | undefined {
  return (globalThis as typeof globalThis & { Path2D?: typeof Path2D }).Path2D
}

describe('fxRenderer module-level caches', () => {
  beforeEach(() => {
    setPath2DGlobal(getPath2DGlobal() ?? (MockPath2D as unknown as typeof Path2D))
    resetFxRendererCaches()
  })

  it('warms nodeOutline Path2D cache and reset clears it', () => {
    expect(__testing._nodeOutlinePath2DCacheSize()).toBe(0)

    // Minimal LayoutNode-compatible object for sizeForNode/getNodeShape usage.
    const n: LayoutNode = {
      id: 'n1',
      __x: 10,
      __y: 20,
      viz_size: { w: 12, h: 12 },
      // getNodeShape() tolerates missing shape fields; default is 'circle'.
    }

    __testing._warmNodeOutlinePath2DCache(n)
    expect(__testing._nodeOutlinePath2DCacheSize()).toBeGreaterThan(0)

    resetFxRendererCaches()
    expect(__testing._nodeOutlinePath2DCacheSize()).toBe(0)
  })

  it('reset is idempotent', () => {
    resetFxRendererCaches()
    resetFxRendererCaches()
    expect(__testing._nodeOutlinePath2DCacheSize()).toBe(0)
    expect(__testing._nodeOutlineCacheSnapshotKey()).toBeUndefined()
  })
})

// ---------------------------------------------------------------------------
// RT-013-4 — reproducer for F-013-3 (specs/013-frontend-data-honesty/spec.md:180,
// row `RT-013-4` in `## Verification plan`).
//
// A recording Path2D stub. The MockPath2D above swallows every call, so it can
// prove that *a* path was cached but not *which* path. These tests must assert on
// the geometry that actually reaches the canvas, so this stub keeps the arguments
// each path was built with.
//
// WHAT THESE ASSERTIONS CAN AND CANNOT TELL APART
//  - They can tell apart: a path built for the pre-patch size vs a path built for
//    the post-patch size; one Path2D constructed vs several; a cache that keeps an
//    untouched node's path vs one that throws it away.
//  - They cannot tell apart *how* a fix achieves that. Nothing here reads the cache
//    key string, so any fix that makes the drawn geometry follow `sizeForNode`
//    passes — whether it extends the key, keys on a size epoch, or replaces the
//    cache outright. Conversely they do not certify the key itself: a key that
//    happens to be right for these nodes but wrong for others would still pass.
//    The counter-check in claim 2 is what bounds that freedom.
// ---------------------------------------------------------------------------

type RecordedOp = { op: string; args: number[] }

class RecordingPath2D {
  /** Every Path2D built since the last reset — the construction count is the "work redone" signal. */
  static built: RecordingPath2D[] = []
  readonly ops: RecordedOp[] = []

  constructor() {
    RecordingPath2D.built.push(this)
  }

  arc(x: number, y: number, r: number) {
    this.ops.push({ op: 'arc', args: [x, y, r] })
  }
  rect(x: number, y: number, w: number, h: number) {
    this.ops.push({ op: 'rect', args: [x, y, w, h] })
  }
  moveTo(x: number, y: number) {
    this.ops.push({ op: 'moveTo', args: [x, y] })
  }
  lineTo(x: number, y: number) {
    this.ops.push({ op: 'lineTo', args: [x, y] })
  }
  quadraticCurveTo(cx: number, cy: number, x: number, y: number) {
    this.ops.push({ op: 'quadraticCurveTo', args: [cx, cy, x, y] })
  }
  closePath() {
    this.ops.push({ op: 'closePath', args: [] })
  }
  addPath() {
    this.ops.push({ op: 'addPath', args: [] })
  }
}

/**
 * Radius of the circle this path was drawn with.
 * `outlineCache.ts:41-42` builds circle outlines as a single `arc(__x, __y, r)`,
 * and `getNodeScaledGeometry` sets `r = max(w, h) / 2` from `sizeForNode`.
 */
function drawnCircleRadius(p: Path2D): number {
  const ops = (p as unknown as RecordingPath2D).ops
  const arc = ops?.find((o) => o.op === 'arc')
  if (!arc) {
    throw new Error('expected a circle outline built via arc(); got ops: ' + JSON.stringify(ops))
  }
  return arc.args[2]
}

describe('RT-013-4: node outline cache and viz_size (F-013-3)', () => {
  let originalPath2D: typeof Path2D | undefined

  beforeEach(() => {
    originalPath2D = getPath2DGlobal()
    RecordingPath2D.built = []
    setPath2DGlobal(RecordingPath2D as unknown as typeof Path2D)
    resetFxRendererCaches()
  })

  afterEach(() => {
    setPath2DGlobal(originalPath2D)
    resetFxRendererCaches()
  })

  it('claim 1: draws the NEW size after viz_size changes at the same position and zoom', () => {
    // SITUATION. One node, drawn once at scale 1 / invZoom 1. Then a `node.updated`
    // patch enlarges it: `viz_size` is parsed at
    // src/api/normalizeSimulatorEvent.ts:99-107 and written onto the live node in
    // place at src/demo/patches.ts:51. Nothing else moves — same `id`, same
    // `__x`/`__y`, same shape, same `scale`, same `invZoom` — and no new snapshot
    // arrives, so `invalidateNodeOutlineCacheForSnapshotKey` (outlineCache.ts:19,
    // called from fxRenderer/renderFrame.ts:86 with `${equivalent}|${generated_at}`)
    // does not fire.
    const n: LayoutNode = {
      id: 'n1',
      __x: 10,
      __y: 20,
      viz_size: { w: 12, h: 12 },
    }

    // Premise: 12 -> r = max(w, h) / 2 = 6.
    expect(drawnCircleRadius(nodeOutlinePath2D(n))).toBe(6)

    n.viz_size = { w: 40, h: 40 }

    // A CORRECT RENDERER draws a circle of r = 20: `sizeForNode`
    // (src/render/nodeSizing.ts:5) now returns { w: 40, h: 40 } and
    // `getNodeScaledGeometry` (src/render/nodeGeometry.ts:38) turns that into r = 20.
    // THIS RENDERER draws r = 6 — the Path2D cached before the patch. The cache key
    // at src/render/fxRenderer/outlineCache.ts:30 is built from id, rounded __x/__y,
    // shape, scale and invZoom, and from nothing about w/h, so the pre-patch entry is
    // still a hit. DECIDED BY: outlineCache.ts:30.
    expect(drawnCircleRadius(nodeOutlinePath2D(n))).toBe(20)

    // Same defect in the shrink direction, and past the normalisation floor:
    // w = h = 2 is clamped to MIN = 6 by nodeSizing.ts:17-18, so r = 3.
    n.viz_size = { w: 2, h: 2 }
    expect(drawnCircleRadius(nodeOutlinePath2D(n))).toBe(3)
  })

  it('claim 2: viz_size spellings equal after normalisation share ONE cache entry', () => {
    // SITUATION. The same node at the same position and zoom, its `viz_size`
    // rewritten through six spellings that `sizeForNode` (nodeSizing.ts:5) maps onto
    // the SAME effective size: present-and-numeric, missing, null, numeric string,
    // non-numeric string, NaN — DEFAULT = 12 absorbs the last four
    // (nodeSizing.ts:13-18).
    //
    // A CORRECT RENDERER draws one r = 6 circle and keeps exactly one cache entry:
    // on screen these six spellings are one node of one size, and the LRU holds 512
    // entries in total (outlineCache.ts:10) for every node of every frame.
    // A NAIVE FIX that appends the raw `viz_size` to the key rebuilds the identical
    // circle six times and holds six entries — six times the eviction pressure for a
    // picture that never changed. This test is the counter-check that rules that fix
    // out; it passes on today's code, which is why it is a guard and not a
    // reproducer. DECIDED BY: nodeSizing.ts:5 (what "the same size" means) together
    // with outlineCache.ts:30 (what the key may contain).
    const n: LayoutNode = { id: 'n1', __x: 10, __y: 20, viz_size: { w: 12, h: 12 } }

    const asSize = (v: unknown) => v as LayoutNode['viz_size']
    const spellings: LayoutNode['viz_size'][] = [
      { w: 12, h: 12 },
      undefined,
      null,
      asSize({ w: '12', h: '12' }),
      asSize({ w: 'abc', h: 'abc' }),
      { w: Number.NaN, h: Number.NaN },
    ]

    for (const s of spellings) {
      n.viz_size = s
      // Premise, restated per spelling: all six normalise to the same effective size.
      expect(sizeForNode(n)).toEqual({ w: 12, h: 12 })
      expect(drawnCircleRadius(nodeOutlinePath2D(n))).toBe(6)
    }

    // Observable consequence, not the key: the renderer built the path once and
    // reused it, and the LRU holds one entry for these six frames.
    expect(RecordingPath2D.built.length).toBe(1)
    expect(__testing._nodeOutlinePath2DCacheSize()).toBe(1)
  })

  it("claim 3: resizing one node does not throw away another node's cached outline", () => {
    // SITUATION. Two nodes are drawn, then a patch resizes only `a`.
    //
    // A CORRECT RENDERER rebuilds `a`'s outline and leaves `b`'s alone: `b` did not
    // change, and rebuilding every node's Path2D on every per-node patch is exactly
    // what this cache exists to avoid.
    // THE HAMMER FIX — reusing `invalidateNodeOutlineCacheForSnapshotKey`
    // (outlineCache.ts:19-24, which calls `clear()`) or bumping a scene revision on
    // every node patch — would rebuild `b` too, and this test would go red.
    // DECIDED BY: outlineCache.ts:30 (the fix belongs in the key) against
    // outlineCache.ts:19-24 (the cache-wide hammer that must stay unused here).
    const a: LayoutNode = { id: 'a', __x: 0, __y: 0, viz_size: { w: 12, h: 12 } }
    const b: LayoutNode = { id: 'b', __x: 100, __y: 100, viz_size: { w: 20, h: 20 } }

    nodeOutlinePath2D(a)
    nodeOutlinePath2D(b)
    expect(RecordingPath2D.built.length).toBe(2)

    a.viz_size = { w: 40, h: 40 }
    nodeOutlinePath2D(a) // a correct fix builds a new path for `a` here; today's code does not
    const builtAfterResizingA = RecordingPath2D.built.length

    const bAgain = nodeOutlinePath2D(b)
    // `b` must be served from the cache: no Path2D constructed, geometry unchanged.
    // Deliberately not asserting instance identity — a fix may rebuild `b`'s path for
    // some other reason, as long as it does not do so *because* `a` was resized; the
    // construction count is what states that.
    expect(RecordingPath2D.built.length).toBe(builtAfterResizingA)
    expect(drawnCircleRadius(bAgain)).toBe(10)
  })
})
