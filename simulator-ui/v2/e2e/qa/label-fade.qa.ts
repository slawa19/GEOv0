/**
 * 038 S0 - R2 (F-038-2): computed opacity of an amount label in its last frame before the JS TTL removes it from the
 * DOM. The CSS animation is put on the JS clock (`installCssClockSync`), because `page.clock` does not drive it.
 *
 * Until 039 slice C a MEASUREMENT with no threshold: the label was removed at 1904 ms while its CSS fade ran 2600 ms,
 * at opacity 0.110 (clearing: 3808 of 4200 ms, 0.054). Since 2026-10-18 the owner's decision R2 of spec 039 is the
 * verdict (criterion 5, reproducer R5): the label lives as long as its CSS animation - it is not removed before the
 * animation's end, and not while it can still be seen (opacity in its last frame at most 0.05). The old characterisations
 * (`>= 1900`, `>= 3800`, "the CSS fade is longer than the JS TTL") were replaced explicitly here (P-4 of spec 039).
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

/** Opacity of a label in its last frame in the DOM may be at most this (criterion 5 of spec 039). */
const GONE_OPACITY = 0.05

test('R2/R5 - the payment amount label fades to nothing before it is removed: not before its CSS animation ends, at opacity <= 0.05', async ({ page }) => {
  await pushEvent(page, txUpdated(['a', 'b'], '10.00'))
  const t = await traceLabel(page, (l) => l.text.startsWith('-'), 260)
  const row = report('payment', t)
  expect(t.last.animations, 'one CSS animation').toBe(1)
  expect(row.removedByJsAfterMs, `removed not before the end of its CSS animation (${row.cssDurationMs} ms)`).toBeGreaterThanOrEqual(row.cssDurationMs)
  expect(row.removedByJsAfterMs, 'and not a frame later than needed').toBeLessThan(row.cssDurationMs + 32)
  expect(row.opacityInLastFrame, `faded to nothing in its last frame (at most ${GONE_OPACITY})`).toBeLessThanOrEqual(GONE_OPACITY)
  expect(row.maxOpacity, 'counter-check: the label is read while fully opaque, the reader is not stuck at 0').toBe(1)
})

test('R2/R5 - the clearing amount label fades to nothing before it is removed: not before its CSS animation ends, at opacity <= 0.05', async ({ page }) => {
  await pushEvent(page, {
    type: 'clearing.done', plan_id: 'plan-p038-1', cleared_cycles: 1, cleared_amount: '7.00',
    cycle_edges: [{ from: 'a', to: 'b' }, { from: 'b', to: 'c' }, { from: 'c', to: 'a' }],
  })
  // 039 slice C: the amount comes after the spark has gone round the cycle (1.2 s), then lives its 4.2 s animation.
  const t = await traceLabel(page, (l) => l.cls.includes('clearing-premium'), 480)
  const row = report('clearing', t)
  expect(row.removedByJsAfterMs, `removed not before the end of its CSS animation (${row.cssDurationMs} ms)`).toBeGreaterThanOrEqual(row.cssDurationMs)
  expect(row.removedByJsAfterMs, 'and not a frame later than needed').toBeLessThan(row.cssDurationMs + 32)
  expect(row.opacityInLastFrame, `faded to nothing in its last frame (at most ${GONE_OPACITY})`).toBeLessThanOrEqual(GONE_OPACITY)
  expect(row.maxOpacity, 'counter-check: the label is read while fully opaque').toBeGreaterThan(0.99)
})
