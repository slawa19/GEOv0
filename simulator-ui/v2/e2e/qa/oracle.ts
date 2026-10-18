/**
 * 038 S1 - the motion oracle of the stand. Not a test file.
 *
 * It answers five separate questions about a beam, each with its own assertion in `motion.qa.ts` and its own
 * mutation of the product that must turn it red (spec 038, Verification plan):
 *   1. displacement - the painted head moves along the edge between frames;
 *   2. direction    - it moves from the source of the edge to its target;
 *   3. route        - nothing is painted away from the route (other edges of the graph, the chord);
 *   4. order        - edge k lights up strictly after edge k-1, and not before edge k-1 has gone dark: judged by the
 *                     painted INTERVALS of the two legs (`legOrder`), not by how many painted frames they share.
 *                     WEAK BY PIXELS all the same: a beam is painted for only about half of its leg, so legs whose
 *                     LIVES overlap by half are painted one after another and pass (run 2026-10-12: on 2, 3 and 6
 *                     edges). What holds order strictly is the FX-state test in `motion.qa.ts` (one spark alive);
 *   5. composition  - where the FX buffer is painted, the screenshot of the page shows it, on every sampled frame.
 *
 * What it does NOT see: colour, shape and easing of the beam; anything within `NODE_CLEAR_PX` of a route node (the
 * node bursts live there - so a receiver burst that never comes is NOT seen here, only by a unit test); the spot where two edges of the route cross; a beam fainter than `ALPHA_MIN`; an occluder on the named list below (it is excluded, so
 * what happens under it is unknown); a frame between two sampled screenshots.
 */
import { expect, type Page } from '@playwright/test'

import { distToSegment, frames, screenNodes, type Pt } from './stand.js'

/** Painted = alpha at or above this. The FX canvas is cleared to transparent every frame, the graph is on another canvas. */
export const ALPHA_MIN = 12
/**
 * A painted pixel this close to the axis of a route edge belongs to that edge; farther off it is "off the route".
 * Measured 2026-10-11 (`halfWidthPx` in the printed row): the farthest pixel a beam paints at ALPHA_MIN is 2.8-3.0 px
 * from the axis, at low, med and high quality alike. 5 leaves 2 px for rounding of the layout; it was 12, at which a
 * beam drawn 6 px beside its edge was caught 1 time in 10.
 */
export const CORRIDOR_PX = 5
/**
 * Pixels this close to the centre of a route node are not judged: the bursts of sender and receiver are painted there.
 * Also there, at med and high quality: at the start of a leg the bright "packet" behind the head (up to 54 px,
 * `BEAM_SEG_MAX_SPX`) sticks out BEHIND the source node, to about 42 px from its centre. At 40 that gave one pixel
 * "off the route" in one frame of some runs; 46 covers it. It is a property of the product's drawing, named here and
 * in the spec, not judged.
 */
export const NODE_CLEAR_PX = 46
/** A buffer pixel this opaque must be visible on the composed page (see `SHOT_DIFF_MIN`). */
export const STRONG_ALPHA = 64
/** Sum of |dR| + |dG| + |dB| against the screenshot taken before the event that counts as "the page changed here". */
export const SHOT_DIFF_MIN = 24

/**
 * DOM that is known to lie over the scene at 1280x720, BY NAME. A band point under one of these is not judged for
 * visibility. The list is closed on purpose: an occluder that is not named here hides the beam and turns property 5
 * red, which is the point of the property. Do not replace it with `elementsFromPoint`.
 * LIMIT: the names are wide classes. A NEW panel inside one of these containers is excluded silently too, as long as
 * it covers less than 40 % of an edge (`pickRingRoute` refuses a route that is more covered).
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
 * watch. The test refuses to run on a layout that leaves it nothing to look at - since 2026-10-12 that includes a
 * chord that cannot be told from the route (`expectChordTold` below; until then the chord only entered the score).
 */
export async function pickRingRoute(page: Page, ring: readonly string[], hops: number): Promise<RouteGeom> {
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
      // 043 S1 (2026-10-16): an edge that runs over ANOTHER node of the scene is not judgeable - where the beam crosses
      // that node's bright disc the page barely changes (`SHOT_DIFF_MIN`), and property 5 reads it as hidden. Until
      // the HUD shrank, the only such edge of the ring (f>a, over d) lay under the top stack and was never picked.
      let overNodes = 0
      for (let k = 0; k < hops; k += 1) {
        for (const [id, p] of n) if (id !== ids[k] && id !== ids[k + 1] && distToSegment(p, pts[k]!, pts[k + 1]!) < NODE_CLEAR_PX) overNodes += 1
      }
      const score = Math.min(...shares) * 1000 - crossings * 300 - overNodes * 500 + Math.min(chordClear, 200) + Math.min(...lens) / 10
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
  // The refusal of R3 (039 slice A): an open route whose chord cannot be told from it is not handed back.
  expectChordTold(ids, nodes)
  return { ids, edges, nodes, occluders, clip: { x, y, width: Math.ceil(Math.max(...xs)) + 30 - x, height: Math.ceil(Math.max(...ys)) + 30 - y } }
}
/** The geometry of a route given by its node ids, in page pixels, as the oracle judges it. No preconditions are asserted here. */
export async function routeGeomOf(page: Page, ids: readonly string[]): Promise<RouteGeom> {
  const n = await screenNodes(page)
  const nodes = ids.map((id) => {
    const p = n.get(id)
    if (!p) throw new Error(`038 oracle: route node ${id} is not in the layout`)
    return p
  })
  const edges = nodes.slice(0, -1).map((a, k) => ({ name: `${ids[k]}>${ids[k + 1]}`, from: ids[k]!, to: ids[k + 1]!, a, b: nodes[k + 1]! }))
  const xs = nodes.map((p) => p.x)
  const ys = nodes.map((p) => p.y)
  const x = Math.max(0, Math.floor(Math.min(...xs)) - 30)
  const y = Math.max(0, Math.floor(Math.min(...ys)) - 30)
  return { ids: [...ids], edges, nodes, occluders: await namedOccluders(page), clip: { x, y, width: Math.ceil(Math.max(...xs)) + 30 - x, height: Math.ceil(Math.max(...ys)) + 30 - y } }
}

/**
 * THE CHORD (039 slice A, R14). The defect property 3 was built against (F-038-3) is a payment over a route drawn as
 * ONE beam from the sender straight to the receiver. Property 3 sees it only where the chord sender > receiver leaves
 * the corridors of the route: on a layout whose chord runs inside them, a straightened beam is painted "on the route"
 * and properties 1 to 4 can hold for it. The oracle refuses to judge such a layout (`expectChordTold`).
 *
 * A point of the chord is one where a beam drawn along it IS seen off the route when it is
 *  - farther than `CHORD_CLEAR_PX` from the axis of every route edge. Twice the corridor: `CORRIDOR_PX` is the
 *    half-width of a beam at ALPHA_MIN (3 px, measured) plus 2 px for rounding of the layout, so at twice that
 *    distance the whole cross-section of a beam centred on the chord is outside the corridor, with the same 2 px to
 *    spare - derived from the corridor, not from a fixture;
 *  - farther than `NODE_CLEAR_PX` from every route node (nothing is judged there).
 * What is asked for is one unbroken STRETCH of such points at least `MOTION_LIMITS.maxStepPerFrame` of the chord long:
 * the head of a beam that advances by no more than the step the oracle accepts per frame cannot jump over it, so it
 * is painted off the route in at least one probed frame.
 *
 * NOT the middle of the chord alone (the first wording of R14: "refuse when the middle is within 2 x CORRIDOR_PX of a
 * route edge"): a chord that CROSSES a leg of its route at its middle has no clearance there and is told from the
 * route on both sides of the crossing; and a clear middle does not show that the stretch around it is long enough.
 * A closed route (sender = receiver) has no chord and a route of one edge is its chord: neither is judged - the
 * straightening of a closed route would be no beam at all, which property 1 refuses.
 *
 * Not seen: a chord beam fainter than `ALPHA_MIN`; one whose head steps farther per frame than the limit above; a
 * "shortcut" that is not the chord sender > receiver (a beam that skips ONE node of a longer route).
 */
export const CHORD_CLEAR_PX = 2 * CORRIDOR_PX
export type ChordSeen = { judged: boolean; chordPx: number; seenPx: number; share: number }

/** The longest unbroken stretch of the chord first node > last node on which a beam would be painted off the route. */
export function chordSeen(nodes: Pt[]): ChordSeen {
  const a = nodes[0]!
  const b = nodes[nodes.length - 1]!
  const chordPx = Math.hypot(b.x - a.x, b.y - a.y)
  if (nodes.length < 3 || chordPx < 1) return { judged: false, chordPx, seenPx: 0, share: 0 }
  let seenPx = 0
  let run = 0
  for (let d = 0; d <= chordPx; d += 1) {
    const p = { x: a.x + ((b.x - a.x) * d) / chordPx, y: a.y + ((b.y - a.y) * d) / chordPx }
    const seen = nodes.every((n) => Math.hypot(n.x - p.x, n.y - p.y) > NODE_CLEAR_PX)
      && nodes.slice(0, -1).every((from, k) => distToSegment(p, from, nodes[k + 1]!) > CHORD_CLEAR_PX)
    run = seen ? run + 1 : 0
    if (run > seenPx) seenPx = run
  }
  return { judged: true, chordPx, seenPx, share: seenPx / chordPx }
}

/**
 * The refusal: an open route of two or more edges whose chord cannot be told from it is not judged at all. A red
 * here is a property of the LAYOUT (the route is too straight), not of the product; the message says so.
 */
export function expectChordTold(ids: readonly string[], nodes: Pt[]): ChordSeen {
  const closed = ids[0] === ids[ids.length - 1]
  const c = chordSeen(nodes)
  if (closed || !c.judged) return { ...c, judged: false }
  expect(
    c.share,
    `R3 refusal: the chord ${ids[0]} > ${ids[ids.length - 1]} of the route ${ids.join(' > ')} must be told from the route - a payment straightened into one beam `
      + `sender > receiver has to be painted OFF the route somewhere. Longest stretch of the chord farther than ${CHORD_CLEAR_PX} px (2 x CORRIDOR_PX) from every route edge and `
      + `${NODE_CLEAR_PX} px from every route node: ${c.seenPx} px of ${c.chordPx.toFixed(0)} px = ${c.share.toFixed(3)} of the chord; needed ${MOTION_LIMITS.maxStepPerFrame} `
      + '(the largest step of a head per frame the oracle accepts). Below that, property 3 cannot refuse a straightened beam on this layout: the oracle has nothing to judge by. '
      + 'This is the layout, not the product - choose another route or scene; do not lower the number.',
  ).toBeGreaterThanOrEqual(MOTION_LIMITS.maxStepPerFrame)
  return c
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
  /** The farthest painted pixel of the route from the axis of its edge, px: the half-width of what was drawn. */
  maxOffAxis: number
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
    let maxOffAxis = 0
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
        if (bestD > maxOffAxis) maxOffAxis = bestD
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
    return { edges: out, offRoute, offRouteAt, nearNode, crossing, maxOffAxis }
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

/**
 * 039 slice C (2026-10-18): step and probe every frame until no spark of the FX state is alive any more (and `tail`
 * frames after), at most `maxFrames`. A route lasts as long as its legs since slice C (at least 450 ms a leg, one speed,
 * then a fade), so a fixed number of frames (`watchFlight`, 76 = the old 1200 ms) no longer covers it. Throws when the
 * beam is still alive at `maxFrames`: a watch that ends in flight judges half a beam.
 */
export async function watchUntilGone(page: Page, g: RouteGeom, o: { shotEvery?: number; maxFrames?: number; tail?: number } = {}): Promise<FrameProbe[]> {
  const shotEvery = o.shotEvery ?? 1
  const maxFrames = o.maxFrames ?? 400
  const tail = o.tail ?? 3
  const out: FrameProbe[] = []
  let quiet = 0
  for (let f = 1; f <= maxFrames && quiet < tail; f += 1) {
    await frames(page, 1)
    out.push(await probeFrame(page, g, f, shotEvery > 0 && f % shotEvery === 0))
    const alive = await page.evaluate(() => {
      const now = performance.now()
      return (window as unknown as { __geoSim: { fxState: { sparks: Array<{ startedAtMs: number; ttlMs: number }> } } }).__geoSim.fxState.sparks.some((s) => now - s.startedAtMs < s.ttlMs)
    })
    quiet = alive ? 0 : quiet + 1
  }
  if (quiet < tail) throw new Error(`038 oracle: the beam is still alive after ${maxFrames} frames`)
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
  /** Largest advance of the head per frame between two painted frames next to each other (a gap of n frames counts as n). */
  maxStepPerFrame: number
  /** How many different places (to 0.01 of the edge) the head was painted at, and the share of the edge between its extremes. */
  distinctHeads: number
  headCovered: number
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
    let maxStepPerFrame = 0
    for (let i = 1; i < heads.length; i += 1) {
      maxStepBack = Math.max(maxStepBack, heads[i - 1]! - heads[i]!)
      maxStepPerFrame = Math.max(maxStepPerFrame, Math.abs(heads[i]! - heads[i - 1]!) / Math.max(1, lit[i]!.frame - lit[i - 1]!.frame))
    }
    const strong = probes.filter((p) => p.shot && p.edges[e.name]!.strong > 0)
    return {
      name: e.name,
      litFrames: lit.map((p) => p.frame),
      firstLit: lit.length ? lit[0]!.frame : null,
      lastLit: lit.length ? lit[lit.length - 1]!.frame : null,
      headFirst: heads.length ? heads[0]! : null,
      headLast: heads.length ? heads[heads.length - 1]! : null,
      maxStepBack,
      maxStepPerFrame,
      distinctHeads: new Set(heads.map((h) => Math.round(h * 100))).size,
      headCovered: heads.length ? Math.max(...heads) - Math.min(...heads) : 0,
      framesStrong: strong.length,
      framesShown: strong.filter((p) => p.edges[e.name]!.shown * 2 >= p.edges[e.name]!.strong).length,
      strongPx: strong.reduce((s, p) => s + p.edges[e.name]!.strong, 0),
      shownPx: strong.reduce((s, p) => s + p.edges[e.name]!.shown, 0),
    }
  })
}

/**
 * How the head must advance (property 1). Measured on the product at 2026-10-11 with easeOutCubic and legs of 1200,
 * 600 and 400 ms, at low, med and high quality: 13-40 different places, largest step 0.03-0.10 of the edge per
 * frame, 0.69-0.79 of the edge covered (the 40 px next to each node are not judged). At the cap a leg is 200 ms =
 * 12.5 frames: 5-7 places, a largest step of 0.16-0.19, 0.47-0.70 covered - hence its own, weaker limits.
 */
export const MOTION_LIMITS = { minDistinctHeads: 6, maxStepPerFrame: 0.15, minCovered: 0.6 } as const
export const MOTION_LIMITS_AT_CAP = { minDistinctHeads: 4, maxStepPerFrame: 0.3, minCovered: 0.4 } as const

/**
 * PROPERTY 4 BY INTERVALS (039 slice A, BACKLOG 038-1 (a); until 2026-10-12 the rule was "at most one painted frame in
 * common"). A leg is judged by the interval of frames from its first painted frame to its last, holes included: legs
 * painted in alternating frames have no frame in common and intervals that overlap from end to end; a leg painted
 * inside a dark hole of the leg before it has none in common either.
 *
 * `HANDOFF_FRAMES` is how many frames the interval of leg k may share with the interval of leg k-1. What it rests on:
 *  - the schedule of the product (`composables/realFx/useRealTxFx.ts`): leg i starts at i x legMs and lives legMs, so no
 *    INSTANT belongs to two legs, whatever legMs is; and the judged parts of two legs that meet at a node are
 *    2 x NODE_CLEAR_PX apart along the route. From the schedule alone the number would be 0;
 *    039 slice C (2026-10-18): the schedule is now `composables/realFx/txRoutePlan.ts` - leg i starts exactly when leg
 *    i-1 ARRIVES and lives as long as it moves; only the last leg lives on after its arrival (its fade, at the
 *    receiver, where no next leg follows). Still no instant of two legs, so the constant is not moved;
 *  - the grain of the oracle: `watchFlight` probes once a frame, and one frame is the unit a handoff can be told to.
 *    1 is that grain, and it is what the rule of common frames allowed - this rule is nowhere weaker than that one
 *    (two common frames f < g give firstLit(k) <= f < g <= lastLit(k-1), two shared frames here).
 * It does NOT depend on the duration of a leg or of the route: every number here is a frame index compared with a
 * frame index, so a slower tempo moves both sides alike (`leg-order.qa.ts` shows the verdicts at 1x and 3x). A product
 * that lets the tail of a leg live on into the next one (a cross-fade) changes the schedule above: then this constant
 * is moved, with a dated line, by whoever changes the schedule.
 *
 * Not judged HERE: how long the route stays DARK between two legs (measured 2026-10-12: 6-18 frames - a beam faded inside
 * its leg). Since 039 slice C (2026-10-18) that is judged by `motion-c.qa.ts` (HANDOFF), against a bound derived from
 * `NODE_CLEAR_PX` and the leg's own speed in FX state, not from the frames the beam was seen in. Not judged anywhere:
 * legs whose lives overlap while their painted intervals do not (the FX-state test of `motion.qa.ts` holds that).
 */
export const HANDOFF_FRAMES = 1
export type LegOrder = {
  /** Both legs were painted at all: an unpainted leg has no interval, and "no overlap" of nothing is not order. */
  bothPainted: boolean
  startsStrictlyLater: boolean
  afterPreviousWentDark: boolean
  /** Frames that lie in both intervals: lastLit(prev) - firstLit(cur) + 1, not below 0. */
  sharedFrames: number
}

export function legOrder(prev: Pick<EdgeSummary, 'firstLit' | 'lastLit'>, cur: Pick<EdgeSummary, 'firstLit' | 'lastLit'>): LegOrder {
  if (prev.firstLit === null || prev.lastLit === null || cur.firstLit === null || cur.lastLit === null) {
    return { bothPainted: false, startsStrictlyLater: false, afterPreviousWentDark: false, sharedFrames: 0 }
  }
  const sharedFrames = Math.max(0, Math.min(prev.lastLit, cur.lastLit) - Math.max(prev.firstLit, cur.firstLit) + 1)
  return {
    bothPainted: true,
    startsStrictlyLater: cur.firstLit > prev.firstLit,
    // Not "sharedFrames <= HANDOFF_FRAMES" alone: a leg that was over before the previous one began shares nothing.
    afterPreviousWentDark: cur.firstLit >= prev.lastLit - (HANDOFF_FRAMES - 1),
    sharedFrames,
  }
}

const pct = (v: number | null) => (v === null ? 'none' : v.toFixed(3))

/** Property 3 as a value: what `expectBeamOnRoute` compares with 'nothing painted off the route'. */
export function offRouteVerdict(probes: FrameProbe[]): string {
  const off = probes.filter((p) => p.offRoute > 0)
  return off.length === 0
    ? 'nothing painted off the route'
    : `${off[0]!.offRoute} px painted off the route in frame ${off[0]!.frame}, e.g. at (${off[0]!.offRouteAt!.x}, ${off[0]!.offRouteAt!.y}); ${off.length} such frames`
}

/**
 * The five properties, each as its own soft assertion, so that one run names every property that fails.
 * `order: false` is for a route of one edge, where property 4 has nothing to compare; the caller says so in its title.
 * `compositionMinimums: false` is for the measurement at the cap of six edges only: an edge then lasts 200 ms and is
 * strongly painted in 2-4 frames, fewer than the minimums ask for. "The page shows every strong buffer pixel" is still
 * asserted there; "in at least 3 frames, over at least 0.15 of the edge" is reported as a number instead.
 */
export function expectBeamOnRoute(g: RouteGeom, probes: FrameProbe[], o: { order: boolean; compositionMinimums?: boolean }): EdgeSummary[] {
  const sum = summarize(g, probes)
  const lim = o.compositionMinimums === false ? MOTION_LIMITS_AT_CAP : MOTION_LIMITS

  for (const e of sum) {
    // 1. Displacement. Mutation: phase frozen (render/fxRenderer/renderFrame.ts, `t0`).
    expect.soft(e.litFrames.length, `P1 displacement: edge ${e.name} is painted in at least 4 frames (painted in ${e.litFrames.length})`).toBeGreaterThanOrEqual(4)
    const moved = e.headFirst === null || e.headLast === null ? 0 : Math.abs(e.headLast - e.headFirst)
    expect.soft(moved, `P1 displacement: the head on edge ${e.name} moves along it between its first and last painted frame (from ${pct(e.headFirst)} to ${pct(e.headLast)} of the edge)`).toBeGreaterThanOrEqual(0.3)
    // ... and it MOVES there, frame by frame: through several different places, by a bounded step, over most of the
    // edge. Mutations: the head jumps from 0.25 to 0.75 at constant brightness (two places, one step of 0.5); the
    // beam is drawn only between 0.30 and 0.65 of the edge.
    expect.soft(
      { severalPlaces: e.distinctHeads >= lim.minDistinctHeads, boundedStep: e.maxStepPerFrame <= lim.maxStepPerFrame, mostOfTheEdge: e.headCovered >= lim.minCovered },
      `P1 displacement: the head on edge ${e.name} advances frame by frame (${e.distinctHeads} different places, need ${lim.minDistinctHeads}; largest step ${e.maxStepPerFrame.toFixed(3)} of the edge per frame, allowed ${lim.maxStepPerFrame}; covers ${e.headCovered.toFixed(3)} of the edge, need ${lim.minCovered})`,
    ).toEqual({ severalPlaces: true, boundedStep: true, mostOfTheEdge: true })

    // 2. Direction: 0 is the source of the edge, 1 is its target. Mutation: the edge reversed in the renderer.
    expect.soft(
      { startsInSourceHalf: (e.headFirst ?? 1) < 0.5, endsNearerTarget: (e.headLast ?? 0) > (e.headFirst ?? 1), neverStepsBack: e.maxStepBack <= 0.02 },
      `P2 direction: the head on edge ${e.name} goes from its source to its target (first ${pct(e.headFirst)}, last ${pct(e.headLast)}, largest step back ${e.maxStepBack.toFixed(3)})`,
    ).toEqual({ startsInSourceHalf: true, endsNearerTarget: true, neverStepsBack: true })
  }

  // 3. Route: nothing painted off the route during the whole flight. Mutation: sparks on every edge of the graph.
  //    And the chord: a payment drawn as one beam sender > receiver (`chord.qa.ts` shows this very verdict refuse it).
  expect.soft(
    offRouteVerdict(probes),
    `P3 route: pixels of the FX canvas outside the route ${g.ids.join(' > ')} (other edges of the graph, the chord ${g.ids[0]} > ${g.ids[g.ids.length - 1]}) stay dark`,
  ).toBe('nothing painted off the route')

  // 4. Order: strictly one after another, by painted intervals (`legOrder`). Mutations: all edges of the route started
  //    at once; and the one the count of common frames could not tell - all edges alive at once, each painted in every
  //    other frame, so that no frame shows two of them.
  if (o.order) {
    for (let k = 1; k < sum.length; k += 1) {
      const prev = sum[k - 1]!
      const cur = sum[k]!
      const v = legOrder(prev, cur)
      expect.soft(
        { bothPainted: v.bothPainted, startsStrictlyLater: v.startsStrictlyLater, afterPreviousWentDark: v.afterPreviousWentDark },
        `P4 order: edge ${cur.name} lights up strictly after edge ${prev.name} and not while it is still lit (painted intervals in frames: ${prev.name} ${String(prev.firstLit)}..${String(prev.lastLit)}, ${cur.name} ${String(cur.firstLit)}..${String(cur.lastLit)}; the intervals share ${v.sharedFrames} frame(s), allowed ${HANDOFF_FRAMES})`,
      ).toEqual({ bothPainted: true, startsStrictlyLater: true, afterPreviousWentDark: true })
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
