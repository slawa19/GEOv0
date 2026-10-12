/**
 * 039 T3901 - R10 (batching of the edge pass) in the BROWSER: what one static frame of the 100-node scene issues, and
 * an experiment on whether grouping keeps the picture. Renderer tier: `src/p039-repro/r10-*.p039-repro.test.ts`.
 *
 * Corrected form (P-2, P-8): the EDGE pass is counted apart from node outlines; the budget is the number of real style
 * groups; batching is allowed only if it does not change the picture - two alpha-0.2 strokes that cross give 0.36,
 * one combined stroke gives 0.2.
 *
 * Counting: every `stroke()` on the base canvas is classed by the path it strokes - "line" (only moveTo / lineTo: how
 * an edge is drawn) or "shape" (an arc, a rect, a curve: a node outline). Path-building calls are wrapped for that.
 *
 * The experiment (MEASUREMENT, not a verdict on a fix that does not exist): the 348 lines of the scene are drawn three
 * ways on off-screen canvases of the stand - as today (one stroke each, snapshot order), one path per style group
 * (order changed), one path per run of consecutive same-style lines (order kept) - and compared pixel by pixel.
 * LIMITS of the experiment: lines run from rim to rim of a CIRCLE of the node's larger half-size (the product clips a
 * business node as a rounded rectangle); nodes are not drawn over the lines; zoom and pan are the page's. It says how
 * much the EDGE layer changes, not what a user sees after nodes are painted on top.
 */
import { expect, test } from '@playwright/test'

import { withAlpha } from '../../../src/render/color.js'
import { VIZ_MAPPING } from '../../../src/vizMapping.js'
import { boot, camera, fixtureSnapshot100, frames, layoutNodes, loopCounters, pushEvent, txUpdated } from '../stand.js'

type Counts = { baseLineStrokes: number; baseShapeStrokes: number; baseFills: number; baseFillRects: number; baseDrawImages: number; fxDraws: number; lineStyles: Record<string, number>; lineSegments: number }

const note = (id: string, row: unknown) => {
  console.log(`[039 ${id}]`, JSON.stringify(row))
  test.info().annotations.push({ type: `039-${id}`, description: JSON.stringify(row) })
}

test('039 R10 - one static frame of 100 nodes and 348 lines: strokes of the edge pass against style groups (today: one stroke per line)', async ({ page }) => {
  const snapshot = fixtureSnapshot100()
  await boot(page, {
    snapshot, clock: true,
    beforeLoad: (p) => p.addInitScript(() => {
      const w = window as unknown as { __p039: { on: boolean; c: Record<string, unknown> } }
      const fresh = () => ({ baseLineStrokes: 0, baseShapeStrokes: 0, baseFills: 0, baseFillRects: 0, baseDrawImages: 0, fxDraws: 0, lineStyles: {} as Record<string, number>, lineSegments: 0 })
      w.__p039 = { on: false, c: fresh() }
      ;(w as unknown as { __p039Reset: () => void }).__p039Reset = () => { w.__p039.c = fresh() }
      const path = new WeakMap<object, { lines: number; other: number }>()
      const proto = CanvasRenderingContext2D.prototype as unknown as Record<string, (...a: unknown[]) => unknown>
      const wrap = (name: string, before: (ctx: CanvasRenderingContext2D) => void) => {
        const real = proto[name]!
        proto[name] = function (this: CanvasRenderingContext2D, ...a: unknown[]) { before(this); return real.apply(this, a) }
      }
      const cur = (ctx: object) => { let p = path.get(ctx); if (!p) { p = { lines: 0, other: 0 }; path.set(ctx, p) } return p }
      wrap('beginPath', (ctx) => { path.set(ctx, { lines: 0, other: 0 }) })
      wrap('lineTo', (ctx) => { cur(ctx).lines += 1 })
      for (const m of ['arc', 'arcTo', 'rect', 'roundRect', 'ellipse', 'quadraticCurveTo', 'bezierCurveTo', 'closePath']) wrap(m, (ctx) => { cur(ctx).other += 1 })
      const kind = (ctx: CanvasRenderingContext2D) => {
        const cl = (ctx.canvas as HTMLCanvasElement).classList
        return !cl?.contains('canvas') ? 'offscreen' : cl.contains('canvas-fx') ? 'fx' : 'base'
      }
      type C = ReturnType<typeof fresh>
      const count = (ctx: CanvasRenderingContext2D, f: (c: C, ctx: CanvasRenderingContext2D) => void) => {
        if (!w.__p039.on) return
        const k = kind(ctx)
        if (k === 'fx') (w.__p039.c as C).fxDraws += 1
        else if (k === 'base') f(w.__p039.c as C, ctx)
      }
      wrap('stroke', (ctx) => count(ctx, (c) => {
        const p = cur(ctx)
        if (p.other === 0 && p.lines > 0) {
          c.baseLineStrokes += 1
          c.lineSegments += p.lines
          const style = `${String(ctx.strokeStyle)}|${ctx.lineWidth.toFixed(4)}`
          c.lineStyles[style] = (c.lineStyles[style] ?? 0) + 1
        } else c.baseShapeStrokes += 1
      }))
      wrap('fill', (ctx) => count(ctx, (c) => { c.baseFills += 1 }))
      wrap('fillRect', (ctx) => count(ctx, (c) => { c.baseFillRects += 1 }))
      wrap('drawImage', (ctx) => count(ctx, (c) => { c.baseDrawImages += 1 }))
    }),
  })

  // The stand's own way to one static pass (`budgets.qaperf.ts`): after a payment has gone, the loop idles at 4 fps.
  const l = snapshot.links[0]!
  await pushEvent(page, txUpdated([l.source, l.target], '10.00', { amount_flyout: false }))
  await page.clock.runFor(48 + 1200 + 420 + 250 + 500)
  await page.evaluate(() => { const w = window as unknown as { __p039: { on: boolean }; __p039Reset: () => void }; w.__p039Reset(); w.__p039.on = true })
  const d0 = await loopCounters(page)
  await page.clock.runFor(250)
  const d1 = await loopCounters(page)
  const c = await page.evaluate(() => { const w = window as unknown as { __p039: { on: boolean; c: unknown } }; w.__p039.on = false; return w.__p039.c }) as Counts
  const groups = Object.keys(c.lineStyles).length
  note('R10 static frame', {
    passes: d1.draws - d0.draws, allDrawCommandsOfTheStand: d1.cmds - d0.cmds, ...c, styleGroups: groups,
    quality: await page.evaluate(() => document.querySelector<HTMLElement>('.root')?.dataset.quality),
  })
  expect(d1.draws - d0.draws, 'precondition: exactly one pass of the render loop was counted').toBe(1)
  expect(c.lineSegments, 'coverage: all 348 trust lines are drawn in the pass').toBe(snapshot.links.length)
  expect(c.baseShapeStrokes, 'counter-check: node outlines are counted apart (one per node)').toBe(snapshot.nodes.length)
  expect(groups, 'counter-check: the scene has several line styles').toBeGreaterThan(1)
  expect.soft(c.baseLineStrokes, `strokes of the edge pass in one frame; budget = style groups of the scene (${groups})`).toBeLessThanOrEqual(groups)

  // --- the experiment: does grouping change the edge layer? -----------------------------------------------------
  const pos = new Map((await layoutNodes(page)).map((n) => [n.id, n]))
  const cam = await camera(page)
  const size = new Map(snapshot.nodes.map((n) => { const s = n.viz_size as { w: number; h: number } | undefined; return [n.id, Math.max(s?.w ?? 12, s?.h ?? 12) / 2] }))
  const lines = snapshot.links.map((k) => {
    const a = pos.get(k.source)!
    const b = pos.get(k.target)!
    const len = Math.max(1e-6, Math.hypot(b.x - a.x, b.y - a.y))
    const ux = (b.x - a.x) / len
    const uy = (b.y - a.y) / len
    const ra = size.get(k.source)!
    const rb = size.get(k.target)!
    const sx = (x: number) => x * cam.zoom + cam.panX
    const sy = (y: number) => y * cam.zoom + cam.panY
    const alpha = VIZ_MAPPING.link.alpha[String(k.viz_alpha_key ?? 'bg')] ?? VIZ_MAPPING.link.alpha.bg!
    const width = VIZ_MAPPING.link.width_px[String(k.viz_width_key ?? 'hairline')] ?? VIZ_MAPPING.link.width_px.hairline!
    return { x1: sx(a.x + ux * ra), y1: sy(a.y + uy * ra), x2: sx(b.x - ux * rb), y2: sy(b.y - uy * rb), style: withAlpha(VIZ_MAPPING.link.color.default, alpha), width }
  })
  const experiment = await page.evaluate(({ lines, w, h }) => {
    const key = (l: { style: string; width: number }) => `${l.style}|${l.width}`
    const draw = (batches: Array<typeof lines>) => {
      const c = new OffscreenCanvas(w, h)
      const ctx = c.getContext('2d')!
      for (const batch of batches) {
        ctx.strokeStyle = batch[0]!.style
        ctx.lineWidth = batch[0]!.width
        ctx.beginPath()
        for (const l of batch) { ctx.moveTo(l.x1, l.y1); ctx.lineTo(l.x2, l.y2) }
        ctx.stroke()
      }
      return { data: ctx.getImageData(0, 0, w, h).data, strokes: batches.length }
    }
    const perLine = lines.map((l) => [l])
    const byGroup = [...lines.reduce((m, l) => m.set(key(l), [...(m.get(key(l)) ?? []), l]), new Map<string, typeof lines>()).values()]
    const byRun: Array<typeof lines> = []
    for (const l of lines) { const last = byRun[byRun.length - 1]; if (last && key(last[0]!) === key(l)) last.push(l); else byRun.push([l]) }
    const ref = draw(perLine)
    const compare = (x: { data: Uint8ClampedArray; strokes: number }) => {
      let painted = 0
      let differing = 0
      let over8 = 0
      let maxDelta = 0
      // PREMULTIPLIED values are compared (channel x alpha / 255) - what the layer adds to the page. Straight RGB of
      // a nearly transparent pixel is noise of un-premultiplying (a pixel of alpha 1 reads as any colour).
      const pm = (d: Uint8ClampedArray, i: number, ch: number) => (ch === 3 ? d[i + 3]! : (d[i + ch]! * d[i + 3]!) / 255)
      for (let i = 0; i < ref.data.length; i += 4) {
        if (ref.data[i + 3]! > 0 || x.data[i + 3]! > 0) painted += 1
        let d = 0
        for (let ch = 0; ch < 4; ch += 1) d = Math.max(d, Math.abs(pm(ref.data, i, ch) - pm(x.data, i, ch)))
        if (d >= 1) differing += 1
        if (d > 8) over8 += 1
        if (d > maxDelta) maxDelta = d
      }
      return { strokes: x.strokes, paintedPx: painted, differingPx: differing, differingByMoreThan8of255: over8, maxPremultipliedDelta: Math.round(maxDelta * 10) / 10, shareOfPaintedPct: Math.round((differing / Math.max(1, painted)) * 1000) / 10 }
    }
    // Crossings: pairs of lines that properly intersect, by whether the two are of one style group.
    const side = (ax: number, ay: number, bx: number, by: number, cx: number, cy: number) => Math.sign((bx - ax) * (cy - ay) - (by - ay) * (cx - ax))
    let sameStyle = 0
    let otherStyle = 0
    for (let i = 0; i < lines.length; i += 1) {
      for (let j = i + 1; j < lines.length; j += 1) {
        const p = lines[i]!
        const q = lines[j]!
        if (side(p.x1, p.y1, p.x2, p.y2, q.x1, q.y1) * side(p.x1, p.y1, p.x2, p.y2, q.x2, q.y2) < 0 && side(q.x1, q.y1, q.x2, q.y2, p.x1, p.y1) * side(q.x1, q.y1, q.x2, q.y2, p.x2, p.y2) < 0) {
          if (key(p) === key(q)) sameStyle += 1
          else otherStyle += 1
        }
      }
    }
    return {
      canvasPx: w * h, asToday: { strokes: ref.strokes }, onePathPerStyleGroup: compare(draw(byGroup)), onePathPerRunInSnapshotOrder: compare(draw(byRun)),
      controlSameDrawingTwice: compare(draw(perLine)), crossings: { sameStyleGroup: sameStyle, differentStyleGroups: otherStyle },
      whatMaxDiffPixelRatio001WouldAllow: Math.round(w * h * 0.01),
    }
  }, { lines, w: page.viewportSize()!.width, h: page.viewportSize()!.height })
  await frames(page, 1)
  note('R10 batching experiment (edge layer only)', experiment)
  expect(experiment.controlSameDrawingTwice.differingPx, 'counter-check of the comparison: the same drawing twice differs in no pixel').toBe(0)
  expect(experiment.onePathPerStyleGroup.paintedPx, 'counter-check: the experiment painted the lines').toBeGreaterThan(0)
})
