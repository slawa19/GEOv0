/**
 * 039 slice C - MEASUREMENTS of the payment beam on the offline effects workbench (042 T4205), before and after the
 * slice. Debug-only config (`playwright.p039-repro.config.ts`): Chromium without a GPU, Vite dev server out of test
 * mode, `?mode=fixtures` (the offline snapshot of 100 participants, no server). NOT a gate and carries no threshold:
 * the numbers go to the Changelog of spec 039 with the method below.
 *
 * Method. The page clock is installed before load and paused once the layout is still, so every frame is a step of
 * 16 ms and a run is the same at every repetition. The recorded payments of the workbench ("Шаг", one event at a time)
 * are delivered through the live event path (`usePlaylistWorkbench.ts`). After each step the FX canvas is read every
 * frame along the edge of every spark of the FX state (`window.__geoSim.fxState.sparks`): a pixel counts when its alpha
 * is at least `ALPHA_MIN` (the motion oracle's 12), it lies within `CORRIDOR_PX` (5) of the axis of that edge and
 * farther than `NODE_CLEAR_PX` (46) from both of its nodes - the oracle's own corridor (`../oracle.ts`), where the node
 * bursts do not reach. Per leg: how long its head moves (the spark's `moveMs`, before slice C its whole `ttlMs`) and its
 * life (`ttlMs`, the fade of the last leg included), the frames it was alive,
 * lit pixels per alive frame (median and mean), the share of alive frames with at least one lit pixel; per route: the
 * dark frames between two legs (no lit pixel on any leg of the route between the last lit frame of one and the first of
 * the next).
 *
 *   P039C_LABEL=before|after PW_E2E_PORT=<free port> \
 *     npx playwright test --config=e2e/qa/p039-repro/playwright.p039-repro.config.ts "c-motion\.p039"
 *
 * What it does NOT see: the composed page (only the FX buffer; the motion oracle of the stand judges composition);
 * the base canvas (lines lit by the base-canvas overlay are not counted); a pixel inside 46 px of a node.
 */
import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

import { expect, test, type Page } from '@playwright/test'

import { boot, fixtureSnapshot100, frames, loopCounters } from '../stand.js'
import { FRAME_MS, NODE_CLEAR_PX, openWorkbench, probeSparks } from './workbench.js'

const EVENTS = Number.parseInt(process.env.P039C_EVENTS ?? '', 10) || 6
const LABEL = process.env.P039C_LABEL ?? 'run'

test.use({ viewport: { width: 1366, height: 768 } })

type Leg = { edge: string; ttlMs: number; moveMs: number; startedAtMs: number; alive: number[]; px: Record<number, number> }

const median = (xs: number[]) => { if (!xs.length) return 0; const s = [...xs].sort((p, q) => p - q); return s[Math.floor(s.length / 2)]! }
const mean = (xs: number[]) => (xs.length ? xs.reduce((p, q) => p + q, 0) / xs.length : 0)
const r1 = (v: number) => Math.round(v * 10) / 10

export async function measureEvent(page: Page, step: { click: () => Promise<void> }, maxFrames = 400) {
  await step.click()
  const legs = new Map<string, Leg & { lenPx: number }>()
  let quiet = 0
  for (let f = 0; f < maxFrames; f += 1) {
    await page.clock.runFor(FRAME_MS)
    const rows = await probeSparks(page)
    for (const r of rows) {
      const key = `${r.edge}@${Math.round(r.startedAtMs)}`
      const l = legs.get(key) ?? { edge: r.edge, ttlMs: r.ttlMs, moveMs: r.moveMs, startedAtMs: r.startedAtMs, alive: [], px: {}, lenPx: r.lenPx }
      if (r.alive) { l.alive.push(f); l.px[f] = r.px }
      legs.set(key, l)
    }
    const anyAlive = rows.some((r) => r.alive)
    quiet = anyAlive || legs.size === 0 ? 0 : quiet + 1
    if (quiet > 30) break
  }
  const ordered = [...legs.values()].sort((p, q) => p.startedAtMs - q.startedAtMs)
  const perLeg = ordered.map((l) => {
    const pxs = l.alive.map((f) => l.px[f] ?? 0)
    const lit = l.alive.filter((f) => (l.px[f] ?? 0) > 0)
    return {
      edge: l.edge.replace(/PID_(U\d+)_\w+/g, '$1'), lenPx: Math.round(l.lenPx), moveMs: r1(l.moveMs), ttlMs: r1(l.ttlMs), aliveFrames: l.alive.length,
      medianPxPerAliveFrame: median(pxs), meanPxPerAliveFrame: r1(mean(pxs)), maxPx: Math.max(0, ...pxs),
      litShare: r1((lit.length / Math.max(1, l.alive.length)) * 100) / 100, firstLit: lit[0] ?? null, lastLit: lit[lit.length - 1] ?? null,
    }
  })
  const darkGaps = perLeg.slice(1).map((l, i) => {
    const p = perLeg[i]!
    return p.lastLit === null || l.firstLit === null ? null : Math.max(0, l.firstLit - p.lastLit - 1)
  })
  const first = ordered[0]
  const last = ordered[ordered.length - 1]
  return { legs: perLeg, darkGapsFrames: darkGaps, routeMs: first && last ? r1(last.startedAtMs + last.ttlMs - first.startedAtMs) : 0 }
}

const outDir = () => { const d = test.info().project.outputDir; mkdirSync(d, { recursive: true }); return d }

for (const theme of ['saas', 'hud'] as const) {
  for (const quality of ['low', 'med', 'high'] as const) {
    test(`039 C measurement - beam on the workbench, ${theme}, quality ${quality}: lit pixels per frame along a leg, leg duration, dark gaps`, async ({ page }) => {
      test.setTimeout(900_000)
      const { step } = await openWorkbench(page, { theme, quality })
      const events = []
      for (let i = 0; i < EVENTS; i += 1) events.push(await measureEvent(page, step))
      // Legs long enough to have a judged part (the oracle's own precondition, `pickRingRoute`: > 2 x 46 + 60 px);
      // a shorter one is all node zone and reads 0 whatever is drawn.
      const all = events.flatMap((e) => e.legs).filter((l) => l.lenPx > 2 * NODE_CLEAR_PX + 60)
      const summary = {
        label: LABEL, theme, quality, events: EVENTS, legs: all.length,
        legMoveMs: { min: Math.min(...all.map((l) => l.moveMs)), median: median(all.map((l) => l.moveMs)), max: Math.max(...all.map((l) => l.moveMs)) },
        pxPerAliveFrame: { medianOfLegMedians: median(all.map((l) => l.medianPxPerAliveFrame)), meanOfLegMeans: r1(mean(all.map((l) => l.meanPxPerAliveFrame))), minLegMedian: Math.min(...all.map((l) => l.medianPxPerAliveFrame)), maxPx: Math.max(...all.map((l) => l.maxPx)) },
        litShare: { median: median(all.map((l) => l.litShare)), min: Math.min(...all.map((l) => l.litShare)) },
        darkGapsFrames: events.flatMap((e) => e.darkGapsFrames),
        routeMs: events.map((e) => e.routeMs),
      }
      console.log(`[039 C] ${LABEL} ${theme}/${quality}`, JSON.stringify(summary))
      writeFileSync(join(outDir(), `c-motion-${LABEL}-${theme}-${quality}.json`), JSON.stringify({ summary, events }, null, 2))
      expect(all.length, 'counter-check: legs were measured').toBeGreaterThan(0)
    })
  }
}

/**
 * Criterion 8, its first half (`stroke` calls of the base canvas a frame, 348 trust lines): the drawing calls of ONE
 * pass of the base canvas by method, in a static frame of the stand's 100-node scene (no FX, nothing selected), on the
 * stand's own boot. The same test runs on the base and on the slice.
 */
test('039 C measurement - drawing calls of one static pass of the base canvas, by method (100 nodes, 348 lines, quality low)', async ({ page }) => {
  await page.addInitScript(() => {
    const S = { on: false, by: {} as Record<string, number>, comp: {} as Record<string, number> }
    ;(window as unknown as { __p039cCalls: typeof S }).__p039cCalls = S
    const proto = CanvasRenderingContext2D.prototype as unknown as Record<string, (...a: unknown[]) => unknown>
    for (const m of ['stroke', 'fill', 'fillRect', 'drawImage', 'clip']) {
      const real = proto[m]!
      proto[m] = function (this: CanvasRenderingContext2D, ...a: unknown[]) {
        const cl = (this.canvas as HTMLCanvasElement).classList
        if (S.on && cl?.contains('canvas') && !cl.contains('canvas-fx')) {
          S.by[m] = (S.by[m] ?? 0) + 1
          if (m === 'drawImage') S.comp[this.globalCompositeOperation] = (S.comp[this.globalCompositeOperation] ?? 0) + 1
        }
        return real.apply(this, a)
      }
    }
  })
  await boot(page, { snapshot: fixtureSnapshot100(), clock: true })
  await page.clock.runFor(3600)
  const d0 = (await loopCounters(page)).draws
  await page.evaluate(() => {
    const S = (window as unknown as { __p039cCalls: { on: boolean } }).__p039cCalls
    S.on = true
    document.querySelector<HTMLCanvasElement>('canvas.canvas:not(.canvas-fx)')!.dispatchEvent(new PointerEvent('pointermove', { bubbles: true, clientX: 4, clientY: 4, pointerType: 'mouse' }))
  })
  await frames(page, 1)
  const out = await page.evaluate(() => {
    const S = (window as unknown as { __p039cCalls: { on: boolean; by: Record<string, number>; comp: Record<string, number> } }).__p039cCalls
    S.on = false
    return { by: S.by, drawImageComposite: S.comp }
  })
  const passes = (await loopCounters(page)).draws - d0
  console.log(`[039 C] ${LABEL} base-canvas calls in one static pass`, JSON.stringify({ passes, ...out }))
  writeFileSync(join(outDir(), `c-calls-${LABEL}.json`), JSON.stringify({ passes, ...out }, null, 2))
  expect(passes, 'counter-check: one pass').toBe(1)
})
