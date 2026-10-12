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
 *
 * 039 slice A (2026-10-12): "taken" is the product's NEXT READ and nothing weaker - the last two tests. A frame whose
 * connection ended before the product read it used to be answered as taken (the shim settled every pending answer of
 * a dropped connection the same way); a handler that threw was taken too (the product catches and reads on). What
 * "taken" still does not prove - that the event was applied - is written at `installSseShim` in `stand.ts`.
 * Not exercised here: the third outcome, `closed` (handed to the product, connection ended before its loop came
 * back) - it needs a handler that ends its own connection, and no event of the stand makes the product do that.
 */
import { expect, test, type Page } from '@playwright/test'

import { boot, frames, fxCounts, holdSse, pushEvent, pushFrames, releaseSse, ringSnapshot, sseCounts, sseFrame, txUpdated } from './stand.js'

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

type Shim = { __qaSse: { deliver: (t: string) => Promise<{ read: number; closed: number; unread: number }>; release: () => void; breakTransport: () => void } }
/** How a delivery ended, as text: a failed `expect(...).rejects` of Playwright 1.5x reports an error of its own instead of what happened. */
const outcome = (delivery: Promise<unknown>): Promise<string> => delivery.then(() => 'ACCEPTED: the stand counted the delivery as taken', (e: Error) => `REFUSED: ${e.message}`)
const quiet = (n: number) => Array.from({ length: n }, () => sseFrame(txUpdated(['a', 'b'], '10.00', { amount_flyout: false })).frame)

test('taken is the product\'s next read: pushFrames holds taken === pushed, and a frame whose connection is lost before it was read is refused, not counted', async ({ page }) => {
  await boot(page, { snapshot: ringSnapshot(), clock: true })
  const c0 = await sseCounts(page)

  // Control: a batch that arrives. Every frame is answered by a read, and the shim's own count moves by the same number.
  await pushFrames(page, quiet(3))
  const c1 = await sseCounts(page)
  expect({ pushed: c1.pushed - c0.pushed, taken: c1.taken - c0.taken, closed: c1.closed - c0.closed, lost: c1.lost - c0.lost },
    'three frames pushed, three taken').toEqual({ pushed: 3, taken: 3, closed: 0, lost: 0 })

  // The transport is lost with the frame written and not yet read. The frame is held, the write is awaited, and the
  // release and the loss happen in ONE task of the page: the chunk is in the stream and no read has returned it.
  await holdSse(page)
  const unread = outcome(pushEvent(page, txUpdated(['a', 'b'], '10.00', { amount_flyout: false })))
  await page.evaluate(() => 0) // one round trip: the write is in the page, held
  await page.evaluate(() => {
    const sse = (window as unknown as Shim).__qaSse
    sse.release()
    sse.breakTransport()
  })
  expect(await unread, 'pushEvent refuses a frame its connection never read').toMatch(/^REFUSED: .*the connection ended before the product read it/)
  const c2 = await sseCounts(page)
  expect({ pushed: c2.pushed - c1.pushed, taken: c2.taken - c1.taken, closed: c2.closed - c1.closed, lost: c2.lost - c1.lost },
    'the unread frame is counted as lost, not as taken').toEqual({ pushed: 1, taken: 0, closed: 0, lost: 1 })

  // And a batch of which only a part can arrive: two frames written, the transport lost before either is read.
  // (The clock is paused, so the product's reconnect timer has not fired: nobody is listening any more.)
  expect(await outcome(pushFrames(page, quiet(2))), 'pushFrames names the numbers').toMatch(/^REFUSED: .*pushed 2 SSE frame\(s\), the product took 0/)
})

test('a frame whose handler threw is not taken: the product catches the exception and reads on, the stand refuses', async ({ page }) => {
  await boot(page, { snapshot: ringSnapshot(), clock: true })
  // The product's own line (`api/sse.ts`, the catch around `onMessage`) is PLANTED here, between the write and the
  // read: no event of the stand makes the handler throw, and the product is not mutated. What this shows is that the
  // stand hears the line and ties it to the delivery in flight - not that a real exception produces it.
  await holdSse(page)
  const thrown = outcome(pushEvent(page, txUpdated(['a', 'b'], '10.00', { amount_flyout: false })))
  await page.evaluate(() => 0)
  await page.evaluate(() => {
    console.error('[SSE] onMessage error:', 'planted by delivery.qa.ts', 'event:', 'tx.updated', 'id:', 'planted')
    ;(window as unknown as Shim).__qaSse.release()
  })
  expect(await thrown, 'a delivery during which the handler threw is refused').toMatch(/^REFUSED: .*SSE handler threw/)
  // Counter-check: the line fails one delivery, not every later one, and another error of the console fails none.
  await page.evaluate(() => console.error('some other error of the page'))
  await pushEvent(page, txUpdated(['a', 'b'], '10.00', { amount_flyout: false }))
})
