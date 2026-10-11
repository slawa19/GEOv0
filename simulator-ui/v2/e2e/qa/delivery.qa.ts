/**
 * 038 S2 - the instrument under every event test: `pushEvent` returns when the product HAS TAKEN the frame.
 *
 * The SSE stream is read in real time; the tests step a fake clock. Written and stepped at once, a frame that is
 * late in transport is outrun: the first frame of the test is drawn before the event exists for the product. Here the
 * lateness is made, not hoped for: the shim holds the frame out of the stream and the test releases it only after
 * the unawaited write has returned.
 *
 * What this is NOT: the cause of the red R4 on the CI runner (run 38152711087). That was the phase of the paused
 * clock on the frame grid (`stand.ts`, `CLOCK_PHASE_MS`); this race is a different one, closed before it was met.
 *
 * Not seen: a product that would handle a message asynchronously after its read loop moved on (today it is
 * synchronous, `api/sse.ts`); a frame split over several chunks (the shim writes one frame a chunk).
 */
import { expect, test, type Page } from '@playwright/test'

import { boot, frames, fxCounts, holdSse, pushEvent, releaseSse, ringSnapshot, txUpdated } from './stand.js'

/**
 * Write a payment while the transport holds the frame, step ONE frame, report the sparks in the FX state. The frame
 * is released by the test itself, at a point of its own sequence - no timer.
 *  - not awaited: `pushEvent` returns with the frame still held, the frame is stepped, and only then is it released;
 *  - awaited: `pushEvent` is still pending when the frame is released (asserted), and returns after the product took it.
 */
async function sparksOnTheFirstFrame(page: Page, awaitTaken: boolean): Promise<number> {
  const tx = txUpdated(['a', 'b'], '10.00', { amount_flyout: false })
  await holdSse(page)
  if (!awaitTaken) {
    await pushEvent(page, tx, { awaitTaken: false })
    await frames(page, 1)
    const sparks = (await fxCounts(page)).sparks
    await releaseSse(page)
    return sparks
  }
  let returned = false
  const pushed = pushEvent(page, tx).then(() => { returned = true })
  await page.evaluate(() => 0) // one round trip to the page: the write has had its chance to be answered
  expect(returned, 'pushEvent does not return while the transport holds the frame').toBe(false)
  await releaseSse(page)
  await pushed
  await frames(page, 1)
  return (await fxCounts(page)).sparks
}

test('a frame late in transport is not outrun by the fake clock: the first frame after pushEvent already has the spark', async ({ page }) => {
  await boot(page, { snapshot: ringSnapshot(), clock: true })
  expect((await fxCounts(page)).sparks, 'precondition: nothing flies').toBe(0)

  expect(await sparksOnTheFirstFrame(page, true), 'pushEvent returned only after the product took the frame: the frame stepped next draws its spark').toBeGreaterThan(0)

  // Let that beam end (its lifetime is 1200 ms), then the counter-check on the same page.
  await frames(page, 150)
  expect((await fxCounts(page)).sparks, 'the first beam is over').toBe(0)
  expect(await sparksOnTheFirstFrame(page, false), 'counter-check: written and stepped at once, the late frame IS outrun - the hold does delay it').toBe(0)
  await expect.poll(async () => (await fxCounts(page)).sparks, { message: 'and once released it does arrive' }).toBeGreaterThan(0)
})

test('pushEvent refuses a frame written into nothing, unless the test says the loss is meant', async ({ page }) => {
  await boot(page, { snapshot: ringSnapshot(), clock: true })
  await page.goto('about:blank') // the product is gone, and its connection with it; the shim of the stand is on every page
  await expect(pushEvent(page, txUpdated(['a', 'b'])), 'no connection is listening').rejects.toThrow(/was written into nothing/)
  await pushEvent(page, txUpdated(['a', 'b']), { expectLost: true })
})

test('pushEvent answers for a frame the product drops as well: the same event_id twice, and an event of another equivalent', async ({ page }) => {
  await boot(page, { snapshot: ringSnapshot(), clock: true })
  const id = await pushEvent(page, txUpdated(['a', 'b'], '10.00', { amount_flyout: false }))
  await frames(page, 1)
  expect((await fxCounts(page)).sparks, 'the first delivery animates').toBe(1)
  await pushEvent(page, txUpdated(['a', 'b'], '10.00', { amount_flyout: false }), { eventId: id }) // a duplicate: admitted nowhere
  await pushEvent(page, { ...txUpdated(['a', 'b'], '10.00', { amount_flyout: false }), equivalent: 'ZZZ' }) // not of this scene
  await frames(page, 1)
  expect((await fxCounts(page)).sparks, 'neither dropped frame animated, and neither left pushEvent waiting').toBe(1)
})
