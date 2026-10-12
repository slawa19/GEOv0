/**
 * 039 T3901 - R1 (direction of a trust line), PIXEL tier on a small scene. Geometry: `src/p039-repro/r01-direction-geometry...`.
 *
 * Corrected form (P-1, P-2): the arrowhead is at the DEBTOR (the target of `source -> target`); a one-way line and a
 * mutual pair are judged separately (in a mutual pair both ends are a debtor's); the probe does not take node paint.
 *
 * How node paint is kept out. Every node of the scene is the same circle with the same glow, which is the same in
 * every direction. The probe reads two bands just outside a node: one on both sides of the line (3..7 px off its axis,
 * so the 1.1 px line itself is not in it), and a CONTROL band at the same distances turned by 90 degrees, where the
 * node has no line. Without a marker the two bands hold the same glow; a marker makes them differ. The noise of the
 * method is the difference between the two control bands (+90 and -90 degrees), measured in the same frame.
 * A marker is "seen" when the band differs from the control by more than three times that noise (and by at least 1/255
 * on average) - a signal-to-noise criterion, not a colour.
 *
 * LIMITS. It needs nodes with ONE line each (the control direction must be empty) - it is not a probe for the 100-node
 * scene. A marker thinner than the line, or one that lies wholly within 3 px of the axis, is not seen. Colour, shape
 * and size of the marker are not judged. Quality `low` (the stand's browser), theme `hud`, zoom as the layout gives it.
 *
 * SLICE B must turn this test green (it is a plain failing test here, there is no `test.fail` to remove).
 */
import { expect, test, type Page } from '@playwright/test'

import { boot, camera, ringSnapshot, screenNodes, waitLoopAsleep, type Pt, type QaSnapshot } from '../stand.js'

/** One one-way line a -> b (a is the creditor, b the debtor) and one mutual pair c <-> d. Every node has one neighbour. */
function directionScene(): QaSnapshot {
  const s = ringSnapshot()
  const proto = s.links[0]!
  const link = (source: string, target: string, used: string) => ({ ...proto, source, target, used, available: (100 - Number(used)).toFixed(2) })
  return { ...s, nodes: s.nodes.filter((n) => ['a', 'b', 'c', 'd'].includes(n.id)), links: [link('a', 'b', '10.00'), link('c', 'd', '10.00'), link('d', 'c', '5.00')] }
}

type Band = { meanDiffVsControl: number; noise: number; points: number }

/** Mean absolute RGBA difference between the band along the line at `at` (towards `to`) and its control band. */
async function probeEnd(page: Page, at: Pt, to: Pt, nodeRadiusPx: number): Promise<Band> {
  return page.evaluate(({ at, to, r0 }) => {
    const c = document.querySelector<HTMLCanvasElement>('canvas.canvas:not(.canvas-fx)')!
    const k = c.width / Math.max(1, c.clientWidth)
    const box = c.getBoundingClientRect()
    const img = c.getContext('2d')!.getImageData(0, 0, c.width, c.height)
    const px = (x: number, y: number) => {
      const cx = Math.round((x - box.x) * k)
      const cy = Math.round((y - box.y) * k)
      const i = (cy * c.width + cx) * 4
      return [img.data[i] ?? 0, img.data[i + 1] ?? 0, img.data[i + 2] ?? 0, img.data[i + 3] ?? 0]
    }
    const len = Math.hypot(to.x - at.x, to.y - at.y)
    const u = { x: (to.x - at.x) / len, y: (to.y - at.y) / len }
    const rot = (v: { x: number; y: number }, q: number) => ({ x: v.x * Math.cos(q) - v.y * Math.sin(q), y: v.x * Math.sin(q) + v.y * Math.cos(q) })
    const band = (dir: { x: number; y: number }) => {
      const n = { x: -dir.y, y: dir.x }
      const out: number[][] = []
      for (let d = r0 + 1; d <= r0 + 17; d += 1) for (const s of [-7, -6, -5, -4, -3, 3, 4, 5, 6, 7]) out.push(px(at.x + dir.x * d + n.x * s, at.y + dir.y * d + n.y * s))
      return out
    }
    const diff = (p: number[][], q: number[][]) => p.reduce((sum, v, i) => sum + v.reduce((s2, ch, j) => s2 + Math.abs(ch - q[i]![j]!), 0), 0) / (p.length * 4)
    const along = band(u)
    const left = band(rot(u, Math.PI / 2))
    const right = band(rot(u, -Math.PI / 2))
    return { meanDiffVsControl: Math.min(diff(along, left), diff(along, right)), noise: diff(left, right), points: along.length }
  }, { at, to, r0: nodeRadiusPx })
}

const seen = (b: Band) => b.meanDiffVsControl > Math.max(1, 3 * b.noise)

test('039 R1 - pixels: an arrowhead at the debtor end of a one-way line, at both ends of a mutual pair, none at a creditor-only end (today: none anywhere)', async ({ page }) => {
  await boot(page, { snapshot: directionScene(), clock: false })
  await waitLoopAsleep(page)
  const n = await screenNodes(page)
  const zoom = (await camera(page)).zoom
  const r = 12 * zoom // viz_size 24x24 of the stand's nodes
  const [a, b, c, d] = ['a', 'b', 'c', 'd'].map((id) => n.get(id)!)
  const lengths = { ab: Math.hypot(b!.x - a!.x, b!.y - a!.y), cd: Math.hypot(d!.x - c!.x, d!.y - c!.y) }
  expect(Math.min(lengths.ab, lengths.cd), 'the lines are long enough for two disjoint probes (2 x (radius + 17 px) and a gap)').toBeGreaterThan(2 * (r + 17) + 20)

  // Counter-check of the reader: ON the axis of the line the canvas differs from the canvas 12 px beside it - the
  // line is seen. (The base canvas is opaque everywhere - its background is painted - so colour is compared, not alpha.)
  const onAxis = await page.evaluate(({ a, b }) => {
    const cv = document.querySelector<HTMLCanvasElement>('canvas.canvas:not(.canvas-fx)')!
    const k = cv.width / Math.max(1, cv.clientWidth)
    const box = cv.getBoundingClientRect()
    const img = cv.getContext('2d')!.getImageData(0, 0, cv.width, cv.height)
    const rgb = (x: number, y: number) => { const i = (Math.round((y - box.y) * k) * cv.width + Math.round((x - box.x) * k)) * 4; return [img.data[i] ?? 0, img.data[i + 1] ?? 0, img.data[i + 2] ?? 0] }
    const far = (p: number[], q: number[]) => Math.abs(p[0]! - q[0]!) + Math.abs(p[1]! - q[1]!) + Math.abs(p[2]! - q[2]!)
    // At 0.3 of the line, not at its middle: the two components of the scene are laid out across each other, and the
    // lines cross near their middles (seen on a frame, 2026-10-12).
    const mid = { x: a.x + (b.x - a.x) * 0.3, y: a.y + (b.y - a.y) * 0.3 }
    const len = Math.hypot(b.x - a.x, b.y - a.y)
    const nx = -(b.y - a.y) / len
    const ny = (b.x - a.x) / len
    const beside = rgb(mid.x + nx * 12, mid.y + ny * 12)
    return {
      lineVsBeside: Math.max(far(rgb(mid.x, mid.y), beside), far(rgb(mid.x + nx, mid.y + ny), beside), far(rgb(mid.x - nx, mid.y - ny), beside)),
      besideVsOtherSide: far(beside, rgb(mid.x - nx * 12, mid.y - ny * 12)),
    }
  }, { a: a!, b: b! })
  expect(onAxis.lineVsBeside, 'counter-check: the line a -> b is painted on the base canvas (colour on the axis vs 12 px beside it, at 0.3 of the line)').toBeGreaterThan(Math.max(3, 3 * onAxis.besideVsOtherSide))

  const ends = {
    oneWayDebtorEnd: await probeEnd(page, b!, a!, r),
    oneWayCreditorEnd: await probeEnd(page, a!, b!, r),
    mutualFirstEnd: await probeEnd(page, c!, d!, r),
    mutualSecondEnd: await probeEnd(page, d!, c!, r),
  }
  const row = { zoom, nodeRadiusPx: r, lengths, onAxis, ends, seen: Object.fromEntries(Object.entries(ends).map(([k, v]) => [k, seen(v)])) }
  console.log('[039 R1 pixels]', JSON.stringify(row))
  test.info().annotations.push({ type: '039-R1-pixels', description: JSON.stringify(row) })

  expect.soft(seen(ends.oneWayDebtorEnd), `a marker at the debtor end b of a -> b (band vs control ${ends.oneWayDebtorEnd.meanDiffVsControl.toFixed(2)}, noise ${ends.oneWayDebtorEnd.noise.toFixed(2)})`).toBe(true)
  expect.soft(seen(ends.mutualFirstEnd), `a marker at c of the mutual pair (band vs control ${ends.mutualFirstEnd.meanDiffVsControl.toFixed(2)}, noise ${ends.mutualFirstEnd.noise.toFixed(2)})`).toBe(true)
  expect.soft(seen(ends.mutualSecondEnd), `a marker at d of the mutual pair (band vs control ${ends.mutualSecondEnd.meanDiffVsControl.toFixed(2)}, noise ${ends.mutualSecondEnd.noise.toFixed(2)})`).toBe(true)
  // Meaningful only once the three above are green: with no marker anywhere this holds by default.
  expect.soft(seen(ends.oneWayCreditorEnd), 'NO marker at the creditor end a of the one-way line a -> b').toBe(false)
})
