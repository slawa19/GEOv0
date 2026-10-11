/**
 * 038 S1 - the motion oracle of the stand. Not a test file.
 *
 * It answers five separate questions about a beam, each with its own assertion in `motion.qa.ts` and its own
 * mutation of the product that must turn it red (spec 038, Verification plan):
 *   1. displacement - the painted head moves along the edge between frames;
 *   2. direction    - it moves from the source of the edge to its target;
 *   3. route        - nothing is painted away from the route (other edges of the graph, the chord);
 *   4. order        - edge k lights up strictly after edge k-1, and not before edge k-1 has gone dark;
 *   5. composition  - where the FX buffer is painted, the screenshot of the page shows it, on every sampled frame.
 *
 * What it does NOT see: colour, shape and easing of the beam; anything within `NODE_CLEAR_PX` of a route node (the
 * node bursts live there); the spot where two edges of the route cross; a beam fainter than `ALPHA_MIN`; an occluder on the named list below (it is excluded, so
 * what happens under it is unknown); a frame between two sampled screenshots.
 */
import { expect, type Page } from '@playwright/test'

import { distToSegment, frames, screenNodes, type Pt } from './stand.js'

/** Painted = alpha at or above this. The FX canvas is cleared to transparent every frame, the graph is on another canvas. */
export const ALPHA_MIN = 12
/** A painted pixel this close to the axis of a route edge belongs to that edge. */
export const CORRIDOR_PX = 12
/** Pixels this close to the centre of a route node are not judged: the bursts of sender and receiver are painted there. */
export const NODE_CLEAR_PX = 40
/** A buffer pixel this opaque must be visible on the composed page (see `SHOT_DIFF_MIN`). */
export const STRONG_ALPHA = 64
/** Sum of |dR| + |dG| + |dB| against the screenshot taken before the event that counts as "the page changed here". */
export const SHOT_DIFF_MIN = 24

/**
 * DOM that is known to lie over the scene at 1280x720, BY NAME. A band point under one of these is not judged for
 * visibility. The list is closed on purpose: an occluder that is not named here hides the beam and turns property 5
 * red, which is the point of the property. Do not replace it with `elementsFromPoint`.
 */
export const NAMED_OCCLUDERS = ['.graph-navigator', '.ds-ov-top .ds-panel', '.ds-ov-bottom .ds-panel'] as const
const OCCLUDER_MARGIN_PX = 10

export type Rect = { x: number; y: number; w: number; h: number; name: string }
export type EdgeGeom = { name: string; from: string; to: string; a: Pt; b: Pt }
export type RouteGeom = { ids: string[]; edges: EdgeGeom[]; nodes: Pt[]; occluders: Rect[]; clip: { x: number; y: number; width: number; height: number } }

export async function namedOccluders(page: Page): Promise<Rect[]> {
  return page.evaluate(({ sel, m }) => {
    const out: Array<{ x: number; y: number; w: number; h: number; name: string }> = []
    for (const s of sel) {
      for (const el of document.querySelectorAll<HTMLElement>(s)) {
        const r = el.getBoundingClientRect()
        if (r.width > 0 && r.height > 0) out.push({ x: r.x - m, y: r.y - m, w: r.width + 2 * m, h: r.height + 2 * m, name: s })
      }
    }
    return out
  }, { sel: [...NAMED_OCCLUDERS], m: OCCLUDER_MARGIN_PX })
}

function segmentsCross(a: Pt, b: Pt, c: Pt, d: Pt): boolean {
  const side = (p: Pt, q: Pt, r: Pt) => Math.sign((q.x - p.x) * (r.y - p.y) - (q.y - p.y) * (r.x - p.x))
  return side(a, b, c) !== side(a, b, d) && side(c, d, a) !== side(c, d, b)
}

const inRect = (p: Pt, r: Rect) => p.x >= r.x && p.x <= r.x + r.w && p.y >= r.y && p.y <= r.y + r.h

/** Share of the inner part of a segment (away from its nodes) that no named occluder covers. */
function clearShare(a: Pt, b: Pt, occ: Rect[]): number {
  const len = Math.hypot(b.x - a.x, b.y - a.y)
  let all = 0
  let clear = 0
  for (let d = NODE_CLEAR_PX; d <= len - NODE_CLEAR_PX; d += 4) {
    const p = { x: a.x + ((b.x - a.x) * d) / len, y: a.y + ((b.y - a.y) * d) / len }
    all += 1
    if (!occ.some((r) => inRect(p, r))) clear += 1
  }
  return all === 0 ? 0 : clear / all
}

/**
 * A route of `hops` edges over consecutive nodes of the ring: the one whose edges are least under the named HUD and
 * cross each other least, whose chord sender -> receiver runs away from its edges, and whose edges are long enough to
 * watch. The test refuses to run on a layout that leaves it nothing to look at.
 */
export async function pickRingRoute(page: Page, ring: string[], hops: number): Promise<RouteGeom> {
  const n = await screenNodes(page)
  const occluders = await namedOccluders(page)
  let best: { score: number; ids: string[] } | null = null
  for (const dir of [1, -1]) {
    for (let i = 0; i < ring.length; i += 1) {
      const ids = Array.from({ length: hops + 1 }, (_, k) => ring[(((i + dir * k) % ring.length) + ring.length) % ring.length]!)
      const pts = ids.map((id) => n.get(id)!)
      const shares = pts.slice(0, -1).map((p, k) => clearShare(p, pts[k + 1]!, occluders))
      const lens = pts.slice(0, -1).map((p, k) => Math.hypot(pts[k + 1]!.x - p.x, pts[k + 1]!.y - p.y))
      // How far the middle of the chord is from every route edge: a straightened beam must be told from the route.
      const mid = { x: (pts[0]!.x + pts[hops]!.x) / 2, y: (pts[0]!.y + pts[hops]!.y) / 2 }
      const chordClear = Math.min(...pts.slice(0, -1).map((p, k) => distToSegment(mid, p, pts[k + 1]!)))
      let crossings = 0
      for (let p = 0; p < hops; p += 1) for (let q = p + 2; q < hops; q += 1) if (segmentsCross(pts[p]!, pts[p + 1]!, pts[q]!, pts[q + 1]!)) crossings += 1
      const score = Math.min(...shares) * 1000 - crossings * 300 + Math.min(chordClear, 200) + Math.min(...lens) / 10
      if (!best || score > best.score) best = { score, ids }
    }
  }
  const ids = best!.ids
  const nodes = ids.map((id) => n.get(id)!)
  const edges = nodes.slice(0, -1).map((a, k) => ({ name: `${ids[k]}>${ids[k + 1]}`, from: ids[k]!, to: ids[k + 1]!, a, b: nodes[k + 1]! }))
  for (const e of edges) {
    expect(Math.hypot(e.b.x - e.a.x, e.b.y - e.a.y), `route edge ${e.name} is long enough to watch a beam move on it`).toBeGreaterThan(2 * NODE_CLEAR_PX + 60)
    // The part of an edge under a named occluder is excluded from property 5 pixel by pixel (`probeFrame`); what is
    // required here is that most of the edge is left to judge.
    expect(clearShare(e.a, e.b, occluders), `most of route edge ${e.name} is clear of the named HUD (${NAMED_OCCLUDERS.join(', ')})`).toBeGreaterThan(0.6)
  }
  const xs = nodes.map((p) => p.x)
  const ys = nodes.map((p) => p.y)
  const x = Math.max(0, Math.floor(Math.min(...xs)) - 30)
  const y = Math.max(0, Math.floor(Math.min(...ys)) - 30)
  return { ids, edges, nodes, occluders, clip: { x, y, width: Math.ceil(Math.max(...xs)) + 30 - x, height: Math.ceil(Math.max(...ys)) + 30 - y } }
}

export type EdgeFrame = {
  /** Painted pixels of this edge's corridor (alpha >= ALPHA_MIN), away from the route nodes. */
  px: number
  /** Position of the brightest painted pixel along the edge: 0 at its source, 1 at its target. */
  head: number | null
  maxAlpha: number
  /** Pixels at or above STRONG_ALPHA outside the named occluders, and how many of those differ on the screenshot. */
  strong: number
  shown: number
}
export type FrameProbe = {
  frame: number
  edges: Record<string, EdgeFrame>
  /** Painted pixels that belong to no route edge and to no route node: another edge of the graph, the chord, anything. */
  offRoute: number
  offRouteAt: Pt | null
  nearNode: number
  /** Painted pixels where two route edges cross: on the route, attributed to neither edge. */
  crossing: number
  shot: boolean
}

type ProbeArgs = {
  edges: EdgeGeom[]; nodes: Pt[]; occluders: Rect[]; clip: RouteGeom['clip'] | null; shotB64: string | null
  alphaMin: number; corridor: number; nodeClear: number; strong: number; diffMin: number
}

/** Stash the decoded screenshot of the scene before the event; later frames are compared with it. */
export async function rememberBaseline(page: Page, g: RouteGeom): Promise<void> {
  const b64 = (await page.screenshot({ clip: g.clip })).toString('base64')
  await page.evaluate(async (b) => {
    const bmp = await createImageBitmap(await (await fetch(`data:image/png;base64,${b}`)).blob())
    const c = new OffscreenCanvas(bmp.width, bmp.height)
    const ctx = c.getContext('2d')!
    ctx.drawImage(bmp, 0, 0)
    ;(window as unknown as { __qaBase: ImageData }).__qaBase = ctx.getImageData(0, 0, bmp.width, bmp.height)
  }, b64)
}

/** One read of the FX canvas (and, with `shot`, one screenshot of the composed page) sorted by route edge. */
export async function probeFrame(page: Page, g: RouteGeom, frame: number, shot: boolean): Promise<FrameProbe> {
  const shotB64 = shot ? (await page.screenshot({ clip: g.clip })).toString('base64') : null
  const args: ProbeArgs = {
    edges: g.edges, nodes: g.nodes, occluders: g.occluders, clip: shot ? g.clip : null, shotB64,
    alphaMin: ALPHA_MIN, corridor: CORRIDOR_PX, nodeClear: NODE_CLEAR_PX, strong: STRONG_ALPHA, diffMin: SHOT_DIFF_MIN,
  }
  const r = await page.evaluate(async (o: ProbeArgs) => {
    const c = document.querySelector<HTMLCanvasElement>('canvas.canvas-fx')
    if (!c) throw new Error('no FX canvas')
    const ctx = c.getContext('2d')
    if (!ctx) throw new Error('the FX canvas has no 2d context')
    const k = c.width / Math.max(1, c.clientWidth)
    const box = c.getBoundingClientRect()
    const img = ctx.getImageData(0, 0, c.width, c.height)
    let shotPx: ImageData | null = null
    const base = (window as unknown as { __qaBase?: ImageData }).__qaBase
    if (o.shotB64) {
      if (!base) throw new Error('rememberBaseline() was not called before a frame with a screenshot')
      const bmp = await createImageBitmap(await (await fetch(`data:image/png;base64,${o.shotB64}`)).blob())
      const oc = new OffscreenCanvas(bmp.width, bmp.height)
      const octx = oc.getContext('2d')!
      octx.drawImage(bmp, 0, 0)
      shotPx = octx.getImageData(0, 0, bmp.width, bmp.height)
      if (shotPx.width !== base.width || shotPx.height !== base.height) throw new Error('screenshot clip changed size')
    }
    const edges = o.edges.map(() => ({ px: 0, head: null as number | null, maxAlpha: 0, headSum: 0, headN: 0, strong: 0, shown: 0 }))
    let offRoute = 0
    let offRouteAt: { x: number; y: number } | null = null
    let nearNode = 0
    let crossing = 0
    for (let py = 0; py < c.height; py += 1) {
      for (let px = 0; px < c.width; px += 1) {
        const a = img.data[(py * c.width + px) * 4 + 3]!
        if (a < o.alphaMin) continue
        // Page coordinates (CSS px) of this canvas pixel.
        const x = box.x + (px + 0.5) / k
        const y = box.y + (py + 0.5) / k
        if (o.nodes.some((n) => Math.hypot(n.x - x, n.y - y) <= o.nodeClear)) { nearNode += 1; continue }
        let bestI = -1
        let bestD = Infinity
        let secondD = Infinity
        let bestT = 0
        o.edges.forEach((e, i) => {
          const dx = e.b.x - e.a.x
          const dy = e.b.y - e.a.y
          const t = Math.max(0, Math.min(1, ((x - e.a.x) * dx + (y - e.a.y) * dy) / (dx * dx + dy * dy)))
          const d = Math.hypot(x - (e.a.x + t * dx), y - (e.a.y + t * dy))
          if (d < bestD) { secondD = bestD; bestD = d; bestI = i; bestT = t } else if (d < secondD) secondD = d
        })
        if (bestI < 0 || bestD > o.corridor) {
          offRoute += 1
          if (!offRouteAt) offRouteAt = { x: Math.round(x), y: Math.round(y) }
          continue
        }
        // Two route edges cross here (the force layout does not promise a planar ring): the pixel is on the route,
        // but whose it is cannot be told, so it is given to neither.
        if (secondD <= o.corridor + 4) { crossing += 1; continue }
        const e = edges[bestI]!
        e.px += 1
        if (a > e.maxAlpha) { e.maxAlpha = a; e.headSum = bestT; e.headN = 1 } else if (a === e.maxAlpha) { e.headSum += bestT; e.headN += 1 }
        if (shotPx && base && o.clip && a >= o.strong && !o.occluders.some((r) => x >= r.x && x <= r.x + r.w && y >= r.y && y <= r.y + r.h)) {
          const sx = Math.floor(x - o.clip.x)
          const sy = Math.floor(y - o.clip.y)
          if (sx >= 0 && sy >= 0 && sx < shotPx.width && sy < shotPx.height) {
            const j = (sy * shotPx.width + sx) * 4
            const d = Math.abs(shotPx.data[j]! - base.data[j]!) + Math.abs(shotPx.data[j + 1]! - base.data[j + 1]!) + Math.abs(shotPx.data[j + 2]! - base.data[j + 2]!)
            e.strong += 1
            if (d >= o.diffMin) e.shown += 1
          }
        }
      }
    }
    const out: Record<string, { px: number; head: number | null; maxAlpha: number; strong: number; shown: number }> = {}
    o.edges.forEach((e, i) => {
      const s = edges[i]!
      out[e.name] = { px: s.px, head: s.headN ? s.headSum / s.headN : null, maxAlpha: s.maxAlpha, strong: s.strong, shown: s.shown }
    })
    return { edges: out, offRoute, offRouteAt, nearNode, crossing }
  }, args)
  return { frame, shot, ...r }
}

/** Step the paused clock frame by frame and probe every frame; every `shotEvery`-th frame also takes a screenshot. */
export async function watchFlight(page: Page, g: RouteGeom, nFrames: number, shotEvery = 2): Promise<FrameProbe[]> {
  const out: FrameProbe[] = []
  for (let f = 1; f <= nFrames; f += 1) {
    await frames(page, 1)
    out.push(await probeFrame(page, g, f, shotEvery > 0 && f % shotEvery === 0))
  }
  return out
}

export type EdgeSummary = {
  name: string
  litFrames: number[]
  firstLit: number | null
  lastLit: number | null
  /** Head position in the first and the last lit frame, and the largest step backwards between two lit frames. */
  headFirst: number | null
  headLast: number | null
  maxStepBack: number
  /** Frames with a screenshot in which the buffer had strong pixels, and those of them where the page showed at least half. */
  framesStrong: number
  framesShown: number
  strongPx: number
  shownPx: number
  /** Share of the edge between the first and the last head position that the page showed (set by `expectBeamOnRoute`). */
  seenSpan?: number
}

export function summarize(g: RouteGeom, probes: FrameProbe[]): EdgeSummary[] {
  return g.edges.map((e) => {
    const lit = probes.filter((p) => p.edges[e.name]!.px > 0)
    const heads = lit.map((p) => p.edges[e.name]!.head!).filter((h) => h !== null)
    let maxStepBack = 0
    for (let i = 1; i < heads.length; i += 1) maxStepBack = Math.max(maxStepBack, heads[i - 1]! - heads[i]!)
    const strong = probes.filter((p) => p.shot && p.edges[e.name]!.strong > 0)
    return {
      name: e.name,
      litFrames: lit.map((p) => p.frame),
      firstLit: lit.length ? lit[0]!.frame : null,
      lastLit: lit.length ? lit[lit.length - 1]!.frame : null,
      headFirst: heads.length ? heads[0]! : null,
      headLast: heads.length ? heads[heads.length - 1]! : null,
      maxStepBack,
      framesStrong: strong.length,
      framesShown: strong.filter((p) => p.edges[e.name]!.shown * 2 >= p.edges[e.name]!.strong).length,
      strongPx: strong.reduce((s, p) => s + p.edges[e.name]!.strong, 0),
      shownPx: strong.reduce((s, p) => s + p.edges[e.name]!.shown, 0),
    }
  })
}

const pct = (v: number | null) => (v === null ? 'none' : v.toFixed(3))

/**
 * The five properties, each as its own soft assertion, so that one run names every property that fails.
 * `order: false` is for a route of one edge, where property 4 has nothing to compare; the caller says so in its title.
 * `compositionMinimums: false` is for the measurement at the cap of six edges only: an edge then lasts 200 ms and is
 * strongly painted in 2-4 frames, fewer than the minimums ask for. "The page shows every strong buffer pixel" is still
 * asserted there; "in at least 3 frames, over at least 0.15 of the edge" is reported as a number instead.
 */
export function expectBeamOnRoute(g: RouteGeom, probes: FrameProbe[], o: { order: boolean; compositionMinimums?: boolean }): EdgeSummary[] {
  const sum = summarize(g, probes)

  for (const e of sum) {
    // 1. Displacement. Mutation: phase frozen (render/fxRenderer/renderFrame.ts, `t0`).
    expect.soft(e.litFrames.length, `P1 displacement: edge ${e.name} is painted in at least 4 frames (painted in ${e.litFrames.length})`).toBeGreaterThanOrEqual(4)
    const moved = e.headFirst === null || e.headLast === null ? 0 : Math.abs(e.headLast - e.headFirst)
    expect.soft(moved, `P1 displacement: the head on edge ${e.name} moves along it between its first and last painted frame (from ${pct(e.headFirst)} to ${pct(e.headLast)} of the edge)`).toBeGreaterThanOrEqual(0.3)

    // 2. Direction: 0 is the source of the edge, 1 is its target. Mutation: the edge reversed in the renderer.
    expect.soft(
      { startsInSourceHalf: (e.headFirst ?? 1) < 0.5, endsNearerTarget: (e.headLast ?? 0) > (e.headFirst ?? 1), neverStepsBack: e.maxStepBack <= 0.02 },
      `P2 direction: the head on edge ${e.name} goes from its source to its target (first ${pct(e.headFirst)}, last ${pct(e.headLast)}, largest step back ${e.maxStepBack.toFixed(3)})`,
    ).toEqual({ startsInSourceHalf: true, endsNearerTarget: true, neverStepsBack: true })
  }

  // 3. Route: nothing painted off the route during the whole flight. Mutation: sparks on every edge of the graph.
  const off = probes.filter((p) => p.offRoute > 0)
  expect.soft(
    off.length === 0 ? 'nothing painted off the route' : `${off[0]!.offRoute} px painted off the route in frame ${off[0]!.frame}, e.g. at (${off[0]!.offRouteAt!.x}, ${off[0]!.offRouteAt!.y}); ${off.length} such frames`,
    `P3 route: pixels of the FX canvas outside the route ${g.ids.join(' > ')} (other edges of the graph, the chord ${g.ids[0]} > ${g.ids[g.ids.length - 1]}) stay dark`,
  ).toBe('nothing painted off the route')

  // 4. Order: strictly one after another. Mutation: all edges of the route started at once.
  if (o.order) {
    for (let k = 1; k < sum.length; k += 1) {
      const prev = sum[k - 1]!
      const cur = sum[k]!
      const overlap = cur.litFrames.filter((f) => prev.litFrames.includes(f)).length
      expect.soft(
        { startsStrictlyLater: cur.firstLit !== null && prev.firstLit !== null && cur.firstLit > prev.firstLit, overlapAtMostOneFrame: overlap <= 1 },
        `P4 order: edge ${cur.name} lights up strictly after edge ${prev.name} and not while it is still lit (first painted frames ${String(prev.firstLit)} and ${String(cur.firstLit)}, last painted frame of ${prev.name} ${String(prev.lastLit)}, ${overlap} frames in common)`,
      ).toEqual({ startsStrictlyLater: true, overlapAtMostOneFrame: true })
    }
  }

  // 5. Composition: the screenshot of the page changes where the buffer is painted, frame after frame along the edge.
  //    Mutations: FX canvas under the base canvas, FX canvas with opacity 0, a DOM panel over the path.
  for (const e of sum) {
    const shownHeads = probes.filter((p) => p.shot && p.edges[e.name]!.strong > 0 && p.edges[e.name]!.shown * 2 >= p.edges[e.name]!.strong).map((p) => p.edges[e.name]!.head!)
    const span = shownHeads.length ? Math.max(...shownHeads) - Math.min(...shownHeads) : 0
    const minFrames = o.compositionMinimums === false ? 1 : 3
    expect.soft(e.framesStrong, `P5 composition: counter-check - edge ${e.name} has strongly painted buffer pixels in at least ${minFrames} screenshot frame(s) (has ${e.framesStrong})`).toBeGreaterThanOrEqual(minFrames)
    expect.soft(
      { framesShown: e.framesShown, shownPx: e.shownPx },
      `P5 composition: the page shows the beam on edge ${e.name} wherever the FX buffer holds it (buffer: ${e.framesStrong} frames, ${e.strongPx} px; page: ${e.framesShown} frames, ${e.shownPx} px)`,
    ).toEqual({ framesShown: e.framesStrong, shownPx: e.strongPx })
    e.seenSpan = span
    if (o.compositionMinimums !== false) {
      expect.soft(span, `P5 composition: the beam is seen on the page along edge ${e.name}, not at one spot (seen over ${span.toFixed(3)} of the edge)`).toBeGreaterThanOrEqual(0.15)
    }
  }
  return sum
}
