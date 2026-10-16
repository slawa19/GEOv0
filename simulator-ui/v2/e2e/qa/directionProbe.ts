/**
 * 039 slice B, PR 2 - the PIXEL probe of the direction marker of a trust line. Not a test file. Used by the counted
 * stand (`direction.qa.ts`) and by the reproducer it grew from (`p039-repro/r01-direction-pixels.p039-repro.ts`).
 *
 * WHAT IT ANSWERS: is there, at this end of this line, MORE paint than the line itself has on a bare stretch?
 *  - Window: just outside the node, on the line - from `RIM_GLOW_PX` to `RIM_GLOW_PX + WINDOW_LENGTH_PX` beyond the
 *    rim, `WINDOW_HALF_WIDTH_PX` to either side of the axis, sampled every `STEP_PX`. Decision 4 puts the marker from
 *    2 to 7 px beyond the rim and 2 px to either side of the axis: the window holds it from 3 px on, with 3 px of
 *    margin across. (Until 2026-10-12 the window was 26 x 19 px and the threshold was six more points than the bare
 *    stretch, both taken from a synthetic 7 x 5 triangle; the marker of decision 4 is 5 x 4 - half the area - and in a
 *    window that long the line itself outnumbered it. The window is now the size of what is looked for.)
 *  - The node's radius is in SCREEN pixels at any zoom (`render/linkGeometry.ts`); the caller gives it.
 *  - Node paint is cancelled, not excluded: the same window turned by +90 and -90 degrees round the node holds the
 *    same glow and no line (every node of the scene must have ONE neighbour). A sample "stands out" when it differs
 *    from the control at the same place by more than `STANDS_OUT` (sum of |dR| + |dG| + |dB|).
 *  - A window with a line in it stands out anyway. So the end is compared with a BARE STRETCH OF THE SAME LINE: a
 *    window of the same shape `BARE_STRETCH_PX` farther along it, read against the canvas `BESIDE_PX` beside it.
 *  - `strength`: the mean over the bare stretch of the strongest difference across the line - how visible the BODY is
 *    (a line with debt is drawn at a higher alpha than one without; `link_alpha_key` of the backend).
 *  - `solid`: samples of the end window whose colour is `solidRgb` to within `SOLID_TOLERANCE` - the caller gives the
 *    colour of the legend's arrow. A line body is translucent (alpha 0.06-0.32 over the canvas) and never that colour;
 *    the marker is opaque and is. This is what ties the canvas to the legend, and it needs no bare stretch.
 *  - Noise: samples that stand out between the two control windows, and between the two sides of the bare stretch.
 *
 * LIMITS. Every node needs ONE line (the control directions must be empty) - not a probe for the 100-node scene.
 * It counts paint, it does not judge shape or colour: a blob of the same area at the end would be "seen" (the shape is
 * `src/render/baseGraph.direction.test.ts`). A marker fainter than `STANDS_OUT` is not seen. Thresholds are held by
 * the synthetic controls of the callers, at the angles and the zoom of their scene - not proven for every angle.
 */
import { expect, type Page } from '@playwright/test'

import { ARROW } from '../../src/render/linkArrowhead.js'
import { VIZ_LINK } from '../../src/vizMapping.js'
import { camera, frames, layoutNodes, type Pt, type QaSnapshot } from './stand.js'

/** The size the model assumes: the product's own constants, unless a caller overrides the size in the page too. */
export type ArrowSize = { tipGapPx: number; lengthPx: number; widthPx: number; minFreeGapPx: number }
export const SHIPPED_ARROW: ArrowSize = { tipGapPx: ARROW.tipGapPx, lengthPx: ARROW.lengthPx, widthPx: ARROW.widthPx, minFreeGapPx: ARROW.minFreeGapPx }

/** A sample stands out from its control when the sum of |dR| + |dG| + |dB| is above this (a line at alpha 0.32 gives about 96). */
export const STANDS_OUT = 36
/**
 * The window starts this far beyond the rim. Measured 2026-10-12: with 1 px the two control windows of one node
 * differed in 11-21 points (the steep edge of the rim glow, sampled at turned positions); from 3 px on, in 0-1.
 */
export const RIM_GLOW_PX = 3
/** Decision 4: the base of the marker is 7 px beyond the rim. The window ends 1 px after it. */
export const WINDOW_LENGTH_PX = 5
export const WINDOW_HALF_WIDTH_PX = 5
export const STEP_PX = 0.5
/** The bare stretch of the same line starts this far beyond the end window. */
export const BARE_STRETCH_PX = 60
export const BESIDE_PX = 30

export type Synthetic = { at: Pt; to: Pt; tipGapPx: number; lengthPx: number; halfWidthPx: number; fill: string } | null
/** Sum of |dR| + |dG| + |dB| within which a sample is "the colour of the legend's arrow" (an edge pixel of the triangle is a blend). */
export const SOLID_TOLERANCE = 24
export type End = { standsOut: number; bare: number; noise: number; samples: number; strength: number; solid: number }

/**
 * The window at the end `at` of the line `at - to`, on the frame or on a COPY of it with a synthetic triangle painted
 * (tip `tipGapPx` beyond the rim of `synthetic.at`, pointing at that node).
 */
export async function probeEnd(page: Page, at: Pt, to: Pt, nodeRadiusPx: number, synthetic: Synthetic = null, solidRgb: [number, number, number] = [-999, -999, -999]): Promise<End> {
  return page.evaluate(({ at, to, r0, standsOut, synthetic, solidRgb, TOL, GLOW, LEN, HALF, STEP, BARE, BESIDE }) => {
    const c = document.querySelector<HTMLCanvasElement>('canvas.canvas:not(.canvas-fx)')!
    const k = c.width / Math.max(1, c.clientWidth)
    const box = c.getBoundingClientRect()
    const copy = new OffscreenCanvas(c.width, c.height)
    const ctx = copy.getContext('2d')!
    ctx.drawImage(c, 0, 0)
    const unit = (p: { x: number; y: number }, q: { x: number; y: number }) => { const l = Math.hypot(q.x - p.x, q.y - p.y); return { x: (q.x - p.x) / l, y: (q.y - p.y) / l } }
    if (synthetic) {
      const u = unit(synthetic.at, synthetic.to)
      const n = { x: -u.y, y: u.x }
      const X = (x: number) => (x - box.x) * k
      const Y = (y: number) => (y - box.y) * k
      const tip = { x: synthetic.at.x + u.x * (r0 + synthetic.tipGapPx), y: synthetic.at.y + u.y * (r0 + synthetic.tipGapPx) }
      const base = { x: tip.x + u.x * synthetic.lengthPx, y: tip.y + u.y * synthetic.lengthPx }
      ctx.fillStyle = synthetic.fill
      ctx.beginPath()
      ctx.moveTo(X(tip.x), Y(tip.y))
      ctx.lineTo(X(base.x + n.x * synthetic.halfWidthPx), Y(base.y + n.y * synthetic.halfWidthPx))
      ctx.lineTo(X(base.x - n.x * synthetic.halfWidthPx), Y(base.y - n.y * synthetic.halfWidthPx))
      ctx.closePath()
      ctx.fill()
    }
    const img = ctx.getImageData(0, 0, c.width, c.height)
    const px = (x: number, y: number) => {
      const i = (Math.round((y - box.y) * k) * c.width + Math.round((x - box.x) * k)) * 4
      return [img.data[i] ?? 0, img.data[i + 1] ?? 0, img.data[i + 2] ?? 0]
    }
    const u = unit(at, to)
    const rot = (v: { x: number; y: number }, q: number) => ({ x: v.x * Math.cos(q) - v.y * Math.sin(q), y: v.x * Math.sin(q) + v.y * Math.cos(q) })
    /** Rows along the line, each a list of samples across it. */
    const region = (dir: { x: number; y: number }, from = r0 + GLOW, shift = 0) => {
      const n = { x: -dir.y, y: dir.x }
      const rows: number[][][] = []
      for (let d = from; d <= from + LEN; d += STEP) {
        const row: number[][] = []
        for (let s = -HALF; s <= HALF; s += STEP) row.push(px(at.x + dir.x * d + n.x * (s + shift), at.y + dir.y * d + n.y * (s + shift)))
        rows.push(row)
      }
      return rows
    }
    const delta = (v: number[], q: number[]) => Math.abs(v[0]! - q[0]!) + Math.abs(v[1]! - q[1]!) + Math.abs(v[2]! - q[2]!)
    const count = (p: number[][][], q: number[][][]) => p.reduce((sum, row, i) => sum + row.filter((v, j) => delta(v, q[i]![j]!) > standsOut).length, 0)
    const along = region(u)
    const left = region(rot(u, Math.PI / 2))
    const right = region(rot(u, -Math.PI / 2))
    // The same shape of window farther along the SAME line, where there is only the line: against the canvas beside it.
    const far = r0 + GLOW + LEN + BARE
    const stretch = region(u, far)
    const beside = [region(u, far, BESIDE), region(u, far, -BESIDE)]
    const strongest = (q: number[][][]) => stretch.reduce((sum, row, i) => sum + Math.max(...row.map((v, j) => delta(v, q[i]![j]!))), 0) / stretch.length
    return {
      standsOut: Math.min(count(along, left), count(along, right)),
      bare: Math.min(count(stretch, beside[0]!), count(stretch, beside[1]!)),
      noise: count(left, right) + count(beside[0]!, beside[1]!),
      samples: along.length * along[0]!.length,
      strength: Math.round(Math.min(strongest(beside[0]!), strongest(beside[1]!)) * 10) / 10,
      solid: along.reduce((sum, row) => sum + row.filter((v) => delta(v, solidRgb) <= TOL).length, 0),
    }
  }, { at, to, r0: nodeRadiusPx, standsOut: STANDS_OUT, synthetic, solidRgb, TOL: SOLID_TOLERANCE, GLOW: RIM_GLOW_PX, LEN: WINDOW_LENGTH_PX, HALF: WINDOW_HALF_WIDTH_PX, STEP: STEP_PX, BARE: BARE_STRETCH_PX, BESIDE: BESIDE_PX })
}

/**
 * A marker at an end: more standing-out samples there than on a bare stretch of the same line, by `minExtra` and over
 * three times the noise. `minExtra` is the caller's, with its own synthetic control.
 */
export const markerSeen = (end: End, minExtra: number) => end.standsOut - end.bare >= minExtra + 3 * end.noise

/** The line must be long enough for an end window and a bare stretch from each end, short of the middle. */
export const minLineLengthPx = (nodeRadiusPx: number) => 2 * (nodeRadiusPx + RIM_GLOW_PX + WINDOW_LENGTH_PX + BARE_STRETCH_PX + WINDOW_LENGTH_PX) + 40

// ---------------------------------------------------------------------------------------------------------------------
// The markers a pass DRAWS, and a model of where they must be
// ---------------------------------------------------------------------------------------------------------------------
//
// For scenes where nodes have many lines (the pixel probe above cannot be used there): the drawing calls of the base
// canvas are recorded, and the tip of every marker is compared with a model written HERE from the layout and the
// camera the product reports. What this does not see: pixels (a marker drawn and then painted over), and an error the
// model shares with the product about where a line meets a node (that rule is `src/render/linkGeometry.test.ts`).

export type Marker = { pts: number[][]; style: string }

/** Record, per pass of the base canvas, every `fill` of a closed three-point path: the direction markers. */
export async function recordMarkers(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const w = window as unknown as { __dir: { cur: Marker[]; passes: number } }
    w.__dir = { cur: [], passes: 0 }
    const path = new WeakMap<object, { ops: string; pts: number[][] }>()
    const proto = CanvasRenderingContext2D.prototype as unknown as Record<string, (...a: unknown[]) => unknown>
    const isBase = (ctx: CanvasRenderingContext2D) => { const cl = (ctx.canvas as HTMLCanvasElement).classList; return !!cl?.contains('canvas') && !cl.contains('canvas-fx') }
    const wrap = (name: string, before: (ctx: CanvasRenderingContext2D, a: unknown[]) => void) => {
      const real = proto[name]!
      proto[name] = function (this: CanvasRenderingContext2D, ...a: unknown[]) { before(this, a); return real.apply(this, a) }
    }
    const cur = (ctx: object) => { let p = path.get(ctx); if (!p) { p = { ops: '', pts: [] }; path.set(ctx, p) } return p }
    wrap('clearRect', (ctx) => { if (isBase(ctx)) { w.__dir.cur = []; w.__dir.passes += 1 } })
    wrap('beginPath', (ctx) => { path.set(ctx, { ops: '', pts: [] }) })
    wrap('moveTo', (ctx, a) => { const p = cur(ctx); p.ops += 'M'; p.pts.push([Number(a[0]), Number(a[1])]) })
    wrap('lineTo', (ctx, a) => { const p = cur(ctx); p.ops += 'L'; p.pts.push([Number(a[0]), Number(a[1])]) })
    wrap('closePath', (ctx) => { cur(ctx).ops += 'Z' })
    for (const m of ['arc', 'arcTo', 'rect', 'roundRect', 'ellipse', 'quadraticCurveTo', 'bezierCurveTo']) wrap(m, (ctx) => { cur(ctx).ops += 'X' })
    wrap('fill', (ctx) => {
      if (!isBase(ctx)) return
      const p = cur(ctx)
      if (p.ops === 'MLLZ') w.__dir.cur.push({ pts: p.pts.map((q) => [...q]), style: String(ctx.fillStyle) })
    })
  })
}

export const lastPass = (page: Page) => page.evaluate(() => { const d = (window as unknown as { __dir: { cur: Marker[]; passes: number } }).__dir; return { markers: d.cur, passes: d.passes } })

export type Shape = { shape: string; w: number; h: number }

/**
 * THE MODEL, written here and not imported from the product: where the line from `n` towards `toward` meets `n`
 * (world units). A circle of the larger half-size, or the rectangle of an organisation; sizes are screen px (at least
 * 6, 12 when absent) and so are divided by the zoom.
 */
export function rim(n: Pt & Shape, toward: Pt, zoom: number): Pt {
  const l = Math.hypot(toward.x - n.x, toward.y - n.y)
  const u = { x: (toward.x - n.x) / l, y: (toward.y - n.y) / l }
  const w = Math.max(6, n.w) / zoom
  const h = Math.max(6, n.h) / zoom
  const d = n.shape === 'rounded-rect'
    ? Math.min(Math.abs(u.x) > 1e-6 ? w / 2 / Math.abs(u.x) : Infinity, Math.abs(u.y) > 1e-6 ? h / 2 / Math.abs(u.y) : Infinity)
    : Math.max(w, h) / 2
  return { x: n.x + u.x * d, y: n.y + u.y * d }
}

/** For every line of the snapshot: the free line between the rims in screen px, and where the tip of its marker must be (world). */
export async function modelOf(page: Page, snapshot: Pick<QaSnapshot, 'nodes' | 'links'>, arrow: ArrowSize = SHIPPED_ARROW) {
  const zoom = (await camera(page)).zoom
  const shapes = new Map(snapshot.nodes.map((n) => { const s = n.viz_size as { w?: number; h?: number } | null | undefined; return [n.id, { shape: String(n.viz_shape_key ?? 'circle'), w: Number(s?.w ?? 12), h: Number(s?.h ?? 12) }] }))
  const pos = new Map((await layoutNodes(page)).map((n) => [n.id, { x: n.x, y: n.y, ...shapes.get(n.id)! }]))
  const lines = snapshot.links.map((k) => {
    const s = pos.get(k.source)!
    const t = pos.get(k.target)!
    const l = Math.hypot(t.x - s.x, t.y - s.y)
    const u = { x: (t.x - s.x) / l, y: (t.y - s.y) / l }
    const from = rim(s, t, zoom)
    const to = rim(t, s, zoom)
    const freePx = ((to.x - from.x) * u.x + (to.y - from.y) * u.y) * zoom
    const tip = { x: to.x - (u.x * arrow.tipGapPx) / zoom, y: to.y - (u.y * arrow.tipGapPx) / zoom }
    const base = { x: tip.x - (u.x * arrow.lengthPx) / zoom, y: tip.y - (u.y * arrow.lengthPx) / zoom }
    const half = arrow.widthPx / 2 / zoom
    const corners = [tip, { x: base.x - u.y * half, y: base.y + u.x * half }, { x: base.x + u.y * half, y: base.y - u.x * half }]
    // Under a THIRD node (its painted extent: size + 1.5 px outline, + 4.9 px for a hub's ring): the product draws
    // that marker before the nodes, so the node covers it. Box test for organisations, distance test for circles.
    const underThirdNode = [...pos.entries()].some(([id, n]) => {
      if (id === k.source || id === k.target) return false
      const isHub = String(snapshot.nodes.find((m) => m.id === id)?.type ?? '').toLowerCase() === 'hub'
      const pad = (isHub ? 6.4 : 1.5) / zoom
      const w = Math.max(6, n.w) / zoom / 2 + pad
      const h = Math.max(6, n.h) / zoom / 2 + pad
      if (n.shape === 'rounded-rect') return corners.some((c) => Math.abs(c.x - n.x) <= w) && corners.some((c) => Math.abs(c.y - n.y) <= h)
        && Math.max(...corners.map((c) => c.x)) >= n.x - w && Math.min(...corners.map((c) => c.x)) <= n.x + w
        && Math.max(...corners.map((c) => c.y)) >= n.y - h && Math.min(...corners.map((c) => c.y)) <= n.y + h
      const r = Math.max(w, h)
      const segDist = (a: Pt, b: Pt) => { const dx = b.x - a.x; const dy = b.y - a.y; const l2 = dx * dx + dy * dy; const tt = l2 > 0 ? Math.max(0, Math.min(1, ((n.x - a.x) * dx + (n.y - a.y) * dy) / l2)) : 0; return Math.hypot(a.x + tt * dx - n.x, a.y + tt * dy - n.y) }
      return segDist(corners[0]!, corners[1]!) <= r || segDist(corners[1]!, corners[2]!) <= r || segDist(corners[2]!, corners[0]!) <= r
    })
    return { key: `${k.source}>${k.target}`, freePx, u, tip, sourceRim: from, underThirdNode }
  })
  return { zoom, lines, arrow }
}

/** Every drawn marker must be the marker of ONE line with room, tip at its target; every such line must have one. */
export function compareWithModel(markers: Marker[], model: Awaited<ReturnType<typeof modelOf>>) {
  const bound = model.arrow.minFreeGapPx
  const expected = model.lines.filter((l) => l.freePx >= bound)
  const left = [...markers]
  let unmatched = 0
  let worst = 0
  for (const l of expected) {
    let best = -1
    let bestD = Infinity
    left.forEach((m, i) => { const d = Math.hypot(m.pts[0]![0]! - l.tip.x, m.pts[0]![1]! - l.tip.y); if (d < bestD) { bestD = d; best = i } })
    // A hundredth of a screen pixel: the model and the product compute the same point in floating point.
    if (best >= 0 && bestD * model.zoom < 0.01) { left.splice(best, 1); worst = Math.max(worst, bestD * model.zoom) } else unmatched += 1
  }
  const gaps = model.lines.map((l) => l.freePx).sort((p, q) => p - q)
  return {
    lines: model.lines.length, withRoom: expected.length, withoutRoom: model.lines.length - expected.length, drawn: markers.length,
    linesWithRoomWithoutTheirMarker: unmatched, markersThatAreNoLinesTargetEnd: left.length, worstTipErrorPx: Math.round(worst * 1e5) / 1e5,
    freePx: { min: Math.round(gaps[0]! * 10) / 10, p05: Math.round(gaps[Math.floor(gaps.length * 0.05)]! * 10) / 10, median: Math.round(gaps[Math.floor(gaps.length / 2)]! * 10) / 10 },
    nearestToTheBoundPx: Math.round(Math.min(...gaps.map((g) => Math.abs(g - bound))) * 100) / 100,
    belowBound: gaps.filter((g) => g < bound).map((g) => Math.round(g * 10) / 10),
  }
}

// ---------------------------------------------------------------------------------------------------------------------
// Legibility of a marker against its own line (039 slice B, PR 2, fix-delta of 2026-10-16)
// ---------------------------------------------------------------------------------------------------------------------

/**
 * A marker is DISTINGUISHABLE - as an arrowhead on its line, not as a speck or a thicker bit of line - when all three
 * hold on the rendered pixels:
 *  1. CONTRAST: at least `LEGIBLE_SHARE` of the samples inside its triangle (a 0.5 px grid) stand out (`STANDS_OUT`)
 *     from the samples at the same places of a BARE STRETCH OF THE SAME LINE (the triangle moved back along the line by
 *     one length and `BARE_GAP_PX`). `STANDS_OUT` is the probe's own threshold; half the triangle is its body, not the
 *     anti-aliased rim that any line also has.
 *  2. WIDER THAN A LINE: near its base (rows at 0.8, 0.85 and 0.9 of its length from the tip, the widest kept) the
 *     run of samples of the marker's own colour (`SOLID_TOLERANCE`) across the axis is at least `HEAD_OVER_LINE` times
 *     the widest stroke of a trust line at rest (`WIDEST_LINE_PX`). A head no wider than that reads as the line
 *     itself, thicker for a moment.
 *  3. POINTED: at a quarter of its length from the tip that run is at most half the one at the base.
 * Why these numbers: the factor 2 is the plain "clearly wider" of a size comparison, the quarter/base pair is the
 * smallest taper that is still a taper; they are written here so that a reader can dispute them, and the size the
 * product ships is the smallest one that passes them (`e2e/qa/p039-repro/marker-size.p039-repro.ts`).
 * What it does NOT judge: whether a person at a given distance recognises the shape - the owner's look at the frames.
 * A marker is NOT MEASURED when the bare stretch would leave the free line (less than tip gap + 2 lengths + gap + 2 px
 * between the rims): it is reported as such, never counted as legible. A marker UNDER A THIRD NODE is hidden by
 * design (the node covers it as it covers the line): counted apart (`hidden`), not in the share.
 */
export const LEGIBLE_SHARE = 0.5
export const BARE_GAP_PX = 4
export const HEAD_OVER_LINE = 2
/**
 * The widest stroke a trust line is drawn with AT REST (`thick`, 1.25 px). The 2.2 px highlight is left out on
 * purpose: under a highlight the marker is redrawn in the highlight colour and is compared with that, not with a line.
 */
export const WIDEST_LINE_PX = VIZ_LINK.width_px.thick!

export type Legibility = { measured: number; notMeasured: number; hidden: number; legible: number; share: number; fracs: number[]; baseWidths: number[]; tipWidths: number[]; failedBy: Record<string, number> }

export async function legibility(page: Page, model: Awaited<ReturnType<typeof modelOf>>, markerRgb: [number, number, number], only?: (key: string) => boolean): Promise<Legibility> {
  const cam = await camera(page)
  const a = model.arrow
  const drawn = model.lines.filter((l) => l.freePx >= a.minFreeGapPx && (!only || only(l.key)))
  const hidden = drawn.filter((l) => l.underThirdNode).length
  const tris = drawn.filter((l) => !l.underThirdNode)
    .map((l) => ({ ok: l.freePx >= a.tipGapPx + 2 * a.lengthPx + BARE_GAP_PX + 2, tip: { x: l.tip.x * cam.zoom + cam.panX, y: l.tip.y * cam.zoom + cam.panY }, u: l.u }))
  const rows = await page.evaluate(({ tris, L, W, GAP, standsOut, rgb, TOL }) => {
    const c = document.querySelector<HTMLCanvasElement>('canvas.canvas:not(.canvas-fx)')!
    const k = c.width / Math.max(1, c.clientWidth)
    const box = c.getBoundingClientRect()
    const img = c.getContext('2d')!.getImageData(0, 0, c.width, c.height)
    const px = (x: number, y: number) => {
      const i = (Math.round((y - box.y) * k) * c.width + Math.round((x - box.x) * k)) * 4
      return [img.data[i] ?? 0, img.data[i + 1] ?? 0, img.data[i + 2] ?? 0]
    }
    const diff = (v: number[], w: number[]) => Math.abs(v[0]! - w[0]!) + Math.abs(v[1]! - w[1]!) + Math.abs(v[2]! - w[2]!)
    return tris.map((t) => {
      if (!t.ok) return null
      const n = { x: -t.u.y, y: t.u.x }
      const at = (d: number, s: number) => ({ x: t.tip.x - t.u.x * d + n.x * s, y: t.tip.y - t.u.y * d + n.y * s })
      const back = L + GAP
      let all = 0
      let out = 0
      for (let d = 0; d <= L; d += 0.5) {
        const half = (W / 2) * (d / L)
        for (let s = -half; s <= half; s += 0.5) {
          const p = at(d, s)
          all += 1
          if (diff(px(p.x, p.y), px(p.x - t.u.x * back, p.y - t.u.y * back)) > standsOut) out += 1
        }
      }
      // The longest run of samples of the marker's colour across the axis, 0.25 px apart, centred on it.
      const run = (d: number) => {
        let best = 0
        let cur = 0
        for (let s = -W; s <= W; s += 0.25) {
          const p = at(d, s)
          if (diff(px(p.x, p.y), rgb) <= TOL) { cur += 1; best = Math.max(best, cur) } else cur = 0
        }
        return best * 0.25
      }
      return { frac: all ? out / all : 0, base: Math.max(run(0.8 * L), run(0.85 * L), run(0.9 * L)), tip: run(0.25 * L) }
    })
  }, { tris, L: a.lengthPx, W: a.widthPx, GAP: BARE_GAP_PX, standsOut: STANDS_OUT, rgb: markerRgb, TOL: SOLID_TOLERANCE })
  const measured = rows.filter((r): r is { frac: number; base: number; tip: number } => r !== null)
  const failedBy = { contrast: 0, notWiderThanALine: 0, notPointed: 0 }
  let legible = 0
  for (const r of measured) {
    const c1 = r.frac >= LEGIBLE_SHARE
    const c2 = r.base >= HEAD_OVER_LINE * WIDEST_LINE_PX
    const c3 = r.tip <= r.base / 2
    if (!c1) failedBy.contrast += 1
    if (!c2) failedBy.notWiderThanALine += 1
    if (!c3) failedBy.notPointed += 1
    if (c1 && c2 && c3) legible += 1
  }
  const r2 = (v: number) => Math.round(v * 100) / 100
  return {
    measured: measured.length, notMeasured: rows.length - measured.length, hidden, legible, share: measured.length ? legible / measured.length : 0,
    fracs: measured.map((r) => r2(r.frac)), baseWidths: measured.map((r) => r.base), tipWidths: measured.map((r) => r.tip), failedBy,
  }
}

/**
 * TEST-ONLY override of the size of the marker in the page: the dev server's copy of `src/render/linkArrowhead.ts`
 * is served with another `ARROW_LENGTH_PX` (everything else there is derived from it). No product switch exists or is
 * added. Must be installed before the app loads (`boot({ beforeLoad })`).
 */
export async function overrideArrowLength(page: Page, lengthPx: number): Promise<void> {
  await page.route(/\/src\/render\/linkArrowhead\.ts(\?.*)?$/, async (route) => {
    // Without the conditional headers: a 304 "not modified" has no body to rewrite.
    const headers = Object.fromEntries(Object.entries(route.request().headers()).filter(([k]) => !/^if-(none-match|modified-since)$/i.test(k)))
    const res = await route.fetch({ headers })
    const body = await res.text()
    const pattern = /ARROW_LENGTH_PX\s*=\s*[\d.]+/
    const next = body.replace(pattern, `ARROW_LENGTH_PX = ${lengthPx}`)
    if (!pattern.test(body)) throw new Error(`039 marker size: ARROW_LENGTH_PX was not found in the served module (status ${res.status()}, ${body.length} bytes) - the override would be silent`)
    await route.fulfill({ status: 200, headers: { 'content-type': res.headers()['content-type'] ?? 'text/javascript', 'cache-control': 'no-store' }, body: next })
  })
}

export const arrowOfLength = (lengthPx: number): ArrowSize => ({ tipGapPx: ARROW.tipGapPx, lengthPx, widthPx: lengthPx * (ARROW.widthPx / ARROW.lengthPx), minFreeGapPx: 2 * (ARROW.tipGapPx + lengthPx) })

/** Bring the application's zoom to `target` with ONE wheel event (the camera multiplies by exp(-deltaY / 1000)). */
export async function zoomTo(page: Page, target: number): Promise<number> {
  const z0 = (await camera(page)).zoom
  const vp = page.viewportSize()!
  await page.mouse.move(vp.width / 2, vp.height / 2)
  await page.mouse.wheel(0, -1000 * Math.log(target / z0))
  await frames(page, 12)
  const z = (await camera(page)).zoom
  expect(Math.abs(z - target), `the application zoom reached ${target} (it is ${z})`).toBeLessThan(0.005)
  return z
}
