/**
 * 039 T3901 - R5 (an amount label fades out before it is removed). Owner's decision R2 (2026-10-12): the lifetime of a
 * label equals the duration of its CSS animation; the label fades to nothing and only then is removed. Payment and clearing.
 *
 * Corrected form (P-2): "opacity in the frame of removal" is undefined - the element is gone. What is judged, in order:
 * the label WAS visible (peak opacity), its opacity in its LAST PRESENT frame, and the time it was removed against the
 * duration of its CSS animation. The measurement of the stand (`../label-fade.qa.ts`, 038 R2) is the instrument.
 *
 * The threshold 0.05 is the spec's. Next to it the test reports what it is derived from: the opacity the animation
 * itself has one frame (16 ms) before its end - what a label removed exactly at the end of the CSS shows last.
 *
 * LIMITS. CSS animations are put on the JS clock by the stand (`installCssClockSync`); a transition is not seen. One
 * label of each kind, on the ring of six, quality `low`. The removal time is known to one frame (16 ms).
 */
import { expect, test, type Page } from '@playwright/test'

import { boot, frames, pushEvent, ringSnapshot, syncCssToClock, txUpdated, type LabelSample } from '../stand.js'

type Trace = { samples: LabelSample[]; last: LabelSample; removedAfterMs: number; opacityOneFrameBeforeCssEnd: number }

async function traceLabel(page: Page, match: (l: LabelSample) => boolean, maxFrames: number): Promise<Trace> {
  const samples: LabelSample[] = []
  let nearEnd = Number.NaN
  for (let f = 0; f < maxFrames; f += 1) {
    const cur = (await syncCssToClock(page)).find(match)
    if (!cur && samples.length > 0) {
      const last = samples[samples.length - 1]!
      return { samples, last, removedAfterMs: last.ageMs + 16, opacityOneFrameBeforeCssEnd: nearEnd }
    }
    if (cur) {
      if (samples.length === 0) {
        // What the animation itself shows 16 ms before its end: read once, then `syncCssToClock` puts the time back.
        nearEnd = await page.evaluate(() => {
          const el = [...document.querySelectorAll<HTMLElement>('.floating-label-inner')].find((e) => e.getAnimations().length > 0)
          if (!el) return Number.NaN
          for (const a of el.getAnimations()) a.currentTime = Number(a.effect?.getComputedTiming().duration ?? 0) - 16
          return Number(getComputedStyle(el).opacity)
        })
        await syncCssToClock(page)
      }
      samples.push(cur)
    }
    await frames(page, 1)
  }
  throw new Error(`the label was ${samples.length ? 'never removed' : 'never mounted'} within ${maxFrames} frames`)
}

function judge(name: string, t: Trace) {
  const row = {
    label: t.last.text.replace(/\s+/g, ' '), cssDurationMs: t.last.durationMs, removedByJsAfterMs: t.removedAfterMs, lastPresentFrameAgeMs: t.last.ageMs,
    peakOpacity: Math.max(...t.samples.map((s) => s.opacity)), opacityInLastPresentFrame: t.last.opacity,
    opacityOneFrameBeforeCssEnd: t.opacityOneFrameBeforeCssEnd, framesPresent: t.samples.length,
    cssPlayedShare: Number((t.last.ageMs / t.last.durationMs).toFixed(3)),
  }
  console.log(`[039 R5 ${name}]`, JSON.stringify(row))
  test.info().annotations.push({ type: `039-R5-${name}`, description: JSON.stringify(row) })
  expect(t.last.animations, 'counter-check: the label has exactly one CSS animation, and the stand drives it').toBe(1)
  expect(row.peakOpacity, 'first: the label was fully visible (the reader is not stuck at 0)').toBeGreaterThan(0.99)
  expect.soft(row.opacityInLastPresentFrame, `opacity of the ${name} label in its last present frame (threshold 0.05; the animation one frame before its end: ${row.opacityOneFrameBeforeCssEnd})`).toBeLessThanOrEqual(0.05)
  expect.soft(row.removedByJsAfterMs, `the ${name} label is removed not earlier than its CSS animation ends (${row.cssDurationMs} ms)`).toBeGreaterThanOrEqual(row.cssDurationMs)
}

test.beforeEach(async ({ page }) => {
  await boot(page, { snapshot: ringSnapshot(), clock: true })
})

test('039 R5 - the amount label of a PAYMENT fades to nothing before it is removed (today: removed at 1904 ms of 2600, opacity 0.11)', async ({ page }) => {
  await pushEvent(page, txUpdated(['a', 'b'], '10.00'))
  judge('payment', await traceLabel(page, (l) => l.text.startsWith('-'), 260))
})

test('039 R5 - the amount label of a CLEARING fades to nothing before it is removed (today: removed at ~3800 ms of 4200)', async ({ page }) => {
  await pushEvent(page, {
    type: 'clearing.done', plan_id: 'plan-p039-1', cleared_cycles: 1, cleared_amount: '7.00',
    cycle_edges: [{ from: 'a', to: 'b' }, { from: 'b', to: 'c' }, { from: 'c', to: 'a' }],
  })
  judge('clearing', await traceLabel(page, (l) => l.cls.includes('clearing-premium'), 420))
})
