/**
 * 038 S2 - the instrument under every event test: `pushEvent` returns when the product HAS TAKEN the frame.
 *
 * The SSE stream is read in real time; the tests step a fake clock. Written and stepped at once, a frame that is
 * late in transport is outrun: the first frame of the test is drawn before the event exists for the product (the
 * required step on the CI runner, run 38152711087: "there is a spark in the FX state on every one of them -
 * Expected: 0, Received: 1"). Here the lateness is made, not hoped for: the shim holds the frame out of the stream
 * and a real timer of the test releases it 300 ms later, while the test goes on at once.
 *
 * Not seen: a product that would handle a message asynchronously after its read loop moved on (today it is
 * synchronous, `api/sse.ts`); a frame split over several chunks (the shim writes one frame a chunk).
 */
import { expect, test, type Page } from '@playwright/test'

import { boot, frames, fxCounts, holdSse, pushEvent, releaseSse, ringSnapshot, txUpdated } from './stand.js'

/** Write a payment while the transport holds frames for 300 ms, step ONE frame, report the sparks in the FX state. */
async function sparksOnTheFirstFrame(page: Page, awaitTaken: boolean): Promise<number> {
  await holdSse(page)
  const released = new Promise<void>((done, fail) => { setTimeout(() => { releaseSse(page).then(done, fail) }, 300) })
  await pushEvent(page, txUpdated(['a', 'b'], '10.00', { amount_flyout: false }), awaitTaken ? undefined : { awaitTaken: false })
  await frames(page, 1)
  const sparks = (await fxCounts(page)).sparks
  await released
  return sparks
}

test('a frame late in transport is not outrun by the fake clock: the first frame after pushEvent already has the spark', async ({ page }) => {
  await boot(page, { snapshot: ringSnapshot(), clock: true })
  expect((await fxCounts(page)).sparks, 'precondition: nothing flies').toBe(0)

  expect(await sparksOnTheFirstFrame(page, true), 'pushEvent returned only after the product took the frame: the frame stepped next draws its spark').toBeGreaterThan(0)

  // Let that beam end (its lifetime is 1200 ms), then the counter-check on the same page.
  await frames(page, 150)
  expect((await fxCounts(page)).sparks, 'the first beam is over').toBe(0)
  expect(await sparksOnTheFirstFrame(page, false), 'counter-check: written and stepped at once, the late frame IS outrun - the hold does delay it').toBe(0)
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
