/**
 * 039 T3901 - R1 (direction of a trust line), PIXEL tier on a small scene. Geometry: `src/p039-repro/r01-direction-geometry...`.
 *
 * Corrected form (P-1, P-2): the arrowhead is at the DEBTOR (the target of `source -> target`); a one-way line and a
 * mutual pair are judged separately (in a mutual pair both ends are a debtor's); the probe does not take node paint.
 *
 * SLICE B must turn this test green (it is a plain failing test here, there is no `test.fail` to remove).
 *
 * THE PROBE (reworked after the review of 2026-10-12; it no longer prescribes a shape of the marker).
 *  - Region: at an end of a line, the patch of the canvas just outside the node - 26 px along the line starting 3 px
 *    beyond the rim, 9 px to either side of the axis, THE AXIS INCLUDED. A compact arrowhead that lies on the axis
 *    (2 px to either side) is inside it as much as a wide one is.
 *  - The node's radius is in SCREEN pixels and does not depend on zoom: the product divides the node size by the zoom
 *    in world space and the camera multiplies it back (`render/linkGeometry.ts`, `safeInvZoom`; `render/baseGraph.ts`,
 *    `getLinkTermination(a, b, invZ)`). The stand's nodes are 24 px circles: 12 px at any zoom.
 *  - Node paint is cancelled, not excluded: the same region turned by +90 and -90 degrees round the node holds the
 *    same glow and no line (every node of the scene has ONE neighbour). A point "stands out" when it differs from the
 *    control at the same place by more than `STANDS_OUT` (sum of |dR|+|dG|+|dB|).
 *  - A region with a line in it has points that stand out anyway: the line. So an end is compared with a BARE STRETCH
 *    OF THE SAME LINE - a region of the same shape 60 px farther along it, read against the canvas 30 px beside it.
 *    A marker is "seen" when the end has at least `MIN_POINTS` more standing-out points than the bare stretch, over
 *    three times the noise. (Comparing with another line does not work: a line at another angle, or two lines drawn
 *    one over the other as a mutual pair is today, cover a different number of pixels - measured 34 against 41-46.)
 *  - Noise of the method: points that stand out between the control regions themselves, measured in the frame.
 *
 * POSITIVE CONTROL of the probe (anti-vacuum): the same reading on a COPY of the frame with a synthetic marker painted
 * at the debtor end - a compact on-axis triangle (7 px long, 2.5 px to either side) and a wide chevron (10 px, 5 px to
 * either side), in the colour of the line. Both must be seen, and neither at the creditor end. On the product's own
 * frame nothing is added.
 *
 * LIMITS. Nodes with ONE line each are needed (the control directions must be empty) - not a probe for the 100-node
 * scene. A marker fainter than `STANDS_OUT`, or smaller than `MIN_POINTS` pixels, is not seen. Colour and shape are
 * not judged. Quality `low` (the stand's browser), theme `hud`, the zoom the layout gives.
 */
import { expect, test, type Page } from '@playwright/test'

import { boot, camera, ringSnapshot, screenNodes, waitLoopAsleep, type Pt, type QaSnapshot } from '../stand.js'

/** A point stands out from its control when the sum of |dR| + |dG| + |dB| is above this (the line itself gives about 96). */
const STANDS_OUT = 36
/** A marker is at least this many standing-out pixels more than a bare end of the line, over the noise. */
const MIN_POINTS = 6
/**
 * The region starts this far beyond the rim. Measured 2026-10-12: with 1 px the two control regions of one node
 * differed in 11-21 points (the steep edge of the rim glow, sampled at turned positions); from 3 px on, in 0-1.
 */
const RIM_GLOW_PX = 3
/** The bare stretch of the same line the end is compared with starts this far beyond the end region. */
const BARE_STRETCH_PX = 60
/** `viz_size` of the stand's nodes is 24 x 24: a circle of this radius in SCREEN px, at any zoom (see the header). */
const NODE_RADIUS_SCREEN_PX = 12

/** One one-way line a -> b (a is the creditor, b the debtor) and one mutual pair c <-> d. Every node has one neighbour. */
function directionScene(): QaSnapshot {
  const s = ringSnapshot()
  const proto = s.links[0]!
  const link = (source: string, target: string, used: string) => ({ ...proto, source, target, used, available: (100 - Number(used)).toFixed(2) })
  return { ...s, nodes: s.nodes.filter((n) => ['a', 'b', 'c', 'd'].includes(n.id)), links: [link('a', 'b', '10.00'), link('c', 'd', '10.00'), link('d', 'c', '5.00')] }
}

type Synthetic = { at: Pt; to: Pt; lengthPx: number; halfWidthPx: number } | null
type End = { standsOut: number; bare: number; noise: number; points: number }

/** Standing-out points of the region at `at` (towards `to`), on the frame or on a copy of it with a synthetic marker. */
async function probeEnd(page: Page, at: Pt, to: Pt, synthetic: Synthetic = null): Promise<End> {
  return page.evaluate(({ at, to, r0, standsOut, synthetic, GLOW, BARE }) => {
    const c = document.querySelector<HTMLCanvasElement>('canvas.canvas:not(.canvas-fx)')!
    const k = c.width / Math.max(1, c.clientWidth)
    const box = c.getBoundingClientRect()
    const copy = new OffscreenCanvas(c.width, c.height)
    const ctx = copy.getContext('2d')!
    ctx.drawImage(c, 0, 0)
    const unit = (p: { x: number; y: number }, q: { x: number; y: number }) => { const l = Math.hypot(q.x - p.x, q.y - p.y); return { x: (q.x - p.x) / l, y: (q.y - p.y) / l } }
    if (synthetic) {
      // Tip on the rim of the node, pointing at the node; base `lengthPx` back along the line.
      const u = unit(synthetic.at, synthetic.to)
      const n = { x: -u.y, y: u.x }
      const X = (x: number) => (x - box.x) * k
      const Y = (y: number) => (y - box.y) * k
      const tip = { x: synthetic.at.x + u.x * (r0 + 1), y: synthetic.at.y + u.y * (r0 + 1) }
      const base = { x: tip.x + u.x * synthetic.lengthPx, y: tip.y + u.y * synthetic.lengthPx }
      ctx.fillStyle = 'rgba(100, 116, 139, 0.9)' // the colour of a trust line (`vizMapping.ts`, link.color.default)
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
    const region = (dir: { x: number; y: number }, from = r0 + GLOW, shift = 0) => {
      const n = { x: -dir.y, y: dir.x }
      const out: number[][] = []
      for (let d = from; d <= from + 25; d += 1) for (let s = -9; s <= 9; s += 1) out.push(px(at.x + dir.x * d + n.x * (s + shift), at.y + dir.y * d + n.y * (s + shift)))
      return out
    }
    const count = (p: number[][], q: number[][]) => p.filter((v, i) => Math.abs(v[0]! - q[i]![0]!) + Math.abs(v[1]! - q[i]![1]!) + Math.abs(v[2]! - q[i]![2]!) > standsOut).length
    const along = region(u)
    const left = region(rot(u, Math.PI / 2))
    const right = region(rot(u, -Math.PI / 2))
    // The same shape of region farther along the SAME line, where there is only the line: against the canvas beside it.
    const far = r0 + GLOW + 26 + BARE
    const bare = Math.min(count(region(u, far), region(u, far, 30)), count(region(u, far), region(u, far, -30)))
    return { standsOut: Math.min(count(along, left), count(along, right)), bare, noise: count(left, right) + count(region(u, far, 30), region(u, far, -30)), points: along.length }
  }, { at, to, r0: NODE_RADIUS_SCREEN_PX, standsOut: STANDS_OUT, synthetic, GLOW: RIM_GLOW_PX, BARE: BARE_STRETCH_PX })
}

/** A marker at an end: more standing-out points there than on a bare stretch of the same line, by more than the noise. */
const markerSeen = (end: End) => end.standsOut - end.bare >= MIN_POINTS + 3 * end.noise

test('039 R1 - pixels: a marker at the debtor end of a one-way line and at both ends of a mutual pair, none at a creditor-only end (today: none anywhere)', async ({ page }) => {
  await boot(page, { snapshot: directionScene(), clock: false })
  await waitLoopAsleep(page)
  const n = await screenNodes(page)
  const [a, b, c, d] = ['a', 'b', 'c', 'd'].map((id) => n.get(id)!) as [Pt, Pt, Pt, Pt]
  const lengths = { ab: Math.hypot(b.x - a.x, b.y - a.y), cd: Math.hypot(d.x - c.x, d.y - c.y) }
  expect(Math.min(lengths.ab, lengths.cd), 'the lines are long enough for two disjoint regions (an end region and a bare stretch from each end, short of the middle where the two lines cross)').toBeGreaterThan(2 * (NODE_RADIUS_SCREEN_PX + RIM_GLOW_PX + 26 + BARE_STRETCH_PX + 26) + 40)

  const ends = { oneWayDebtorEnd: await probeEnd(page, b, a), oneWayCreditorEnd: await probeEnd(page, a, b), mutualFirstEnd: await probeEnd(page, c, d), mutualSecondEnd: await probeEnd(page, d, c) }
  const synthetic = {
    compactOnAxisAtDebtor: await probeEnd(page, b, a, { at: b, to: a, lengthPx: 7, halfWidthPx: 2.5 }),
    wideArrowheadAtDebtor: await probeEnd(page, b, a, { at: b, to: a, lengthPx: 10, halfWidthPx: 5 }),
    creditorEndWhileMarkerIsAtDebtor: await probeEnd(page, a, b, { at: b, to: a, lengthPx: 10, halfWidthPx: 5 }),
  }
  const row = { zoom: (await camera(page)).zoom, nodeRadiusScreenPx: NODE_RADIUS_SCREEN_PX, lengths, standsOutAbove: STANDS_OUT, minPoints: MIN_POINTS, ends, synthetic }
  console.log('[039 R1 pixels]', JSON.stringify(row))
  test.info().annotations.push({ type: '039-R1-pixels', description: JSON.stringify(row) })

  // Counter-checks of the probe itself.
  for (const [name, e] of Object.entries(ends)) {
    expect(e.bare, `counter-check: the line is seen on its bare stretch near ${name} (points that stand out from the canvas beside it)`).toBeGreaterThan(0)
    expect(e.noise, `counter-check: the control regions at ${name} hold the same paint`).toBeLessThan(MIN_POINTS)
  }
  expect(markerSeen(synthetic.compactOnAxisAtDebtor), 'POSITIVE CONTROL: a compact on-axis triangle painted at the debtor end of a copy of the frame is seen').toBe(true)
  expect(markerSeen(synthetic.wideArrowheadAtDebtor), 'POSITIVE CONTROL: a wide arrowhead painted at the debtor end of a copy of the frame is seen').toBe(true)
  expect(markerSeen(synthetic.creditorEndWhileMarkerIsAtDebtor), 'POSITIVE CONTROL: a marker at the debtor end is not seen at the creditor end').toBe(false)

  const said = (e: End) => `stands out at the end: ${e.standsOut} points, on a bare stretch of the same line: ${e.bare}`
  expect.soft(markerSeen(ends.oneWayDebtorEnd), `a marker at the debtor end b of a -> b (${said(ends.oneWayDebtorEnd)})`).toBe(true)
  expect.soft(markerSeen(ends.mutualFirstEnd), `a marker at c of the mutual pair (${said(ends.mutualFirstEnd)})`).toBe(true)
  expect.soft(markerSeen(ends.mutualSecondEnd), `a marker at d of the mutual pair (${said(ends.mutualSecondEnd)})`).toBe(true)
  // Green on the base by default (there is no marker anywhere); it starts to judge once the three above are green.
  expect.soft(markerSeen(ends.oneWayCreditorEnd), `NO marker at the creditor end a of the one-way line a -> b (${said(ends.oneWayCreditorEnd)})`).toBe(false)
})
