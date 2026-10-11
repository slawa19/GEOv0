/**
 * 038 S0 - R4, a MEASUREMENT (F-038-4): does the render loop stop drawing when nothing moves, and does an event wake
 * it. Counted are completed draws of the loop owner (one clear of the base canvas per `renderFrame`), not registered
 * `requestAnimationFrame` callbacks; the latter are printed next to them.
 */
import { expect, test } from '@playwright/test'

import { boot, frames, loopCounters, pushEvent, ringSnapshot, txUpdated } from './stand.js'

test('R4 - measurement: after 3 s of rest the loop draws nothing for 2 s, and an event wakes it within 50 ms', async ({ page }) => {
  await boot(page, { snapshot: ringSnapshot(), clock: true })

  const start = await loopCounters(page)
  await page.clock.runFor(3600) // IDLE_DELAY_MS 3000 + hold + one idle period of 250 ms
  const rested = await loopCounters(page)
  await page.clock.runFor(2000)
  const asleep = await loopCounters(page)

  await pushEvent(page, txUpdated(['a', 'b']))
  const pushed = await loopCounters(page)
  await frames(page, 3) // 48 ms
  const woken = await loopCounters(page)
  await frames(page, 30)
  const flying = await loopCounters(page)

  const row = {
    drawsWhileSettling: rested.draws - start.draws,
    drawsIn2sAsleep: asleep.draws - rested.draws, rafCallsIn2sAsleep: asleep.calls - rested.calls,
    rafCallsOnEvent: pushed.calls - asleep.calls, drawsIn48msAfterEvent: woken.draws - pushed.draws,
    drawsIn30FramesOfFlight: flying.draws - woken.draws,
  }
  console.log('[038 R4]', JSON.stringify(row))
  test.info().annotations.push({ type: '038-R4', description: JSON.stringify(row) })

  expect(row.drawsIn2sAsleep, 'deep sleep: no draw in 2 s').toBe(0)
  expect(row.rafCallsIn2sAsleep, 'and no frame requested by anyone').toBe(0)
  expect(row.rafCallsOnEvent, 'the event requests a frame at once').toBeGreaterThanOrEqual(1)
  expect(row.drawsIn48msAfterEvent, 'and the loop draws within 50 ms').toBeGreaterThanOrEqual(1)
  expect(row.drawsIn30FramesOfFlight, 'counter-check: the counter moves while a spark flies').toBe(30)
})
