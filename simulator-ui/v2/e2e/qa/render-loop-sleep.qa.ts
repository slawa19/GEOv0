/**
 * 038 S0 - R4, a MEASUREMENT (F-038-4): does the render loop stop drawing when nothing moves, and does an event wake
 * it. Counted are completed draws of the loop owner (one clear of the base canvas per `renderFrame`), not registered
 * `requestAnimationFrame` callbacks; the latter are printed next to them.
 */
import { expect, test } from '@playwright/test'

import { boot, frames, fxCounts, loopCounters, pushEvent, ringSnapshot, txUpdated } from './stand.js'

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

// S1: the beam of a route is spawned leg by leg on timers (useRealTxFx.ts). Sleep and wake are judged by completed
// draws of the loop owner here too: a route started from deep sleep is drawn on EVERY frame of its 1200 ms, the leg
// boundaries included (no frame is lost between the end of one leg and the timer of the next), and when nothing is
// left to animate the loop falls asleep again. Not judged: wall-clock latency of the wake-up (this is `page.clock`).
test('R4 - a 3-edge beam started from deep sleep is drawn on every frame of its flight, and the loop sleeps again afterwards', async ({ page }) => {
  await boot(page, { snapshot: ringSnapshot(), clock: true })
  await page.clock.runFor(3600)
  const rested = await loopCounters(page)
  await page.clock.runFor(2000)
  const asleep = await loopCounters(page)
  expect(asleep.draws - rested.draws, 'precondition: deep sleep, no draw in 2 s').toBe(0)

  await pushEvent(page, txUpdated(['a', 'b', 'c', 'd'], '10.00', { amount_flyout: false }))
  const sparksPerFrame: number[] = []
  for (let f = 0; f < 75; f += 1) { // the whole route: 3 legs of 400 ms
    await frames(page, 1)
    sparksPerFrame.push((await fxCounts(page)).sparks)
  }
  const flown = await loopCounters(page)
  await page.clock.runFor(420 + 250 + 3000 + 600) // the receiver's burst, the hold, the idle delay, one idle period and slack
  const settling = await loopCounters(page)
  await page.clock.runFor(2000)
  const asleepAgain = await loopCounters(page)

  const row = {
    drawsIn75FramesOfFlight: flown.draws - asleep.draws, framesWithoutASparkInState: sparksPerFrame.filter((n) => n === 0).length,
    drawsWhileSettling: settling.draws - flown.draws, drawsIn2sAsleepAgain: asleepAgain.draws - settling.draws, rafCallsIn2sAsleepAgain: asleepAgain.calls - settling.calls,
  }
  console.log('[038 R4 route]', JSON.stringify(row))
  test.info().annotations.push({ type: '038-R4-route', description: JSON.stringify(row) })
  expect(row.drawsIn75FramesOfFlight, 'the loop draws every frame of the route, leg boundaries included').toBe(75)
  expect(row.framesWithoutASparkInState, 'and there is a spark in the FX state on every one of them').toBe(0)
  expect(row.drawsIn2sAsleepAgain, 'deep sleep again when nothing is left to animate').toBe(0)
})
