/**
 * 038 S0 - R2, a MEASUREMENT (F-038-2): computed opacity of an amount label in its last frame before the JS TTL
 * removes it from the DOM. The CSS animation is put on the JS clock (`installCssClockSync`), because `page.clock`
 * does not drive it. No threshold is asserted here: the numbers go to the spec, the owner of the decision.
 */
import { expect, test, type Page } from '@playwright/test'

import { boot, frames, pushEvent, ringSnapshot, syncCssToClock, txUpdated, type LabelSample } from './stand.js'

type Trace = { first: LabelSample; last: LabelSample; samples: LabelSample[]; removedAfterMs: number }

/** Step frame by frame until the label that matches is gone; `last` is its final frame in the DOM. */
async function traceLabel(page: Page, match: (l: LabelSample) => boolean, maxFrames: number): Promise<Trace> {
  const samples: LabelSample[] = []
  for (let f = 0; f < maxFrames; f += 1) {
    const cur = (await syncCssToClock(page)).find(match)
    if (!cur && samples.length > 0) {
      const last = samples[samples.length - 1]!
      return { first: samples[0]!, last, samples, removedAfterMs: last.ageMs + 16 }
    }
    if (cur) samples.push(cur)
    await frames(page, 1)
  }
  throw new Error(`the label was ${samples.length ? 'never removed' : 'never mounted'} within ${maxFrames} frames`)
}

function report(name: string, t: Trace) {
  const at = (ms: number) => t.samples.reduce((b, s) => (Math.abs(s.ageMs - ms) < Math.abs(b.ageMs - ms) ? s : b)).opacity
  const row = {
    label: t.last.text.replace(/\s+/g, ' '), cssDurationMs: t.last.durationMs, lastFrameAgeMs: t.last.ageMs,
    removedByJsAfterMs: t.removedAfterMs, opacityInLastFrame: t.last.opacity,
    cssPlayedShare: Number((t.last.ageMs / t.last.durationMs).toFixed(3)),
    opacityAt: { '25%': at(t.last.durationMs * 0.25), '60%': at(t.last.durationMs * 0.6), '70%': at(t.last.durationMs * 0.7) },
    maxOpacity: Math.max(...t.samples.map((s) => s.opacity)),
  }
  console.log(`[038 R2] ${name}`, JSON.stringify(row))
  test.info().annotations.push({ type: `038-R2-${name}`, description: JSON.stringify(row) })
  return row
}

test.beforeEach(async ({ page }) => {
  await boot(page, { snapshot: ringSnapshot(), clock: true })
})

test('R2 - measurement: opacity of the payment amount label in its last frame before the JS TTL removes it', async ({ page }) => {
  await pushEvent(page, txUpdated(['a', 'b'], '10.00'))
  const t = await traceLabel(page, (l) => l.text.startsWith('-'), 200)
  const row = report('payment', t)
  // Facts of the mechanism, not a verdict: one CSS animation, and a JS TTL that ends before it does.
  expect(t.last.animations).toBe(1)
  expect(row.removedByJsAfterMs, 'JS TTL of the label (useRealTxFx.ts:225)').toBeGreaterThanOrEqual(1900)
  expect(row.removedByJsAfterMs).toBeLessThan(1900 + 32)
  expect(row.cssDurationMs, 'the CSS fade is longer than the JS TTL').toBeGreaterThan(row.removedByJsAfterMs)
  expect(row.maxOpacity, 'counter-check: the label is read while fully opaque, the reader is not stuck at 0').toBe(1)
})

test('R2 - measurement: opacity of the clearing amount label in its last frame before the JS TTL removes it', async ({ page }) => {
  await pushEvent(page, {
    type: 'clearing.done', plan_id: 'plan-p038-1', cleared_cycles: 1, cleared_amount: '7.00',
    cycle_edges: [{ from: 'a', to: 'b' }, { from: 'b', to: 'c' }, { from: 'c', to: 'a' }],
  })
  const t = await traceLabel(page, (l) => l.cls.includes('clearing-premium'), 400)
  const row = report('clearing', t)
  expect(row.removedByJsAfterMs, 'JS TTL of the overlay (useRealClearingFx.ts:134)').toBeGreaterThanOrEqual(3800)
  expect(row.removedByJsAfterMs).toBeLessThan(3800 + 32)
  expect(row.cssDurationMs, 'the CSS fade is longer than the JS TTL').toBeGreaterThan(row.removedByJsAfterMs)
  expect(row.maxOpacity, 'counter-check: the label is read while fully opaque').toBeGreaterThan(0.99)
})
