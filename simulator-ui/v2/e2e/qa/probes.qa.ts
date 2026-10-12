/**
 * 038 S0 - probes of the stand itself: what `page.clock` drives and what it does not, and what a WebDriver browser
 * shows on the page against what its FX buffer holds (S1: through the motion oracle, every frame along an edge).
 * Characterizations of the product on `e2dc3b2a`; none of them is a defect reproducer.
 */
import { chromium, expect, test } from '@playwright/test'

import { pickRingRoute, rememberBaseline, summarize, watchFlight } from './oracle.js'
import { EQ, RING, RUN_ID, boot, frames, fxCounts, loopCounters, pushEvent, ringSnapshot, syncCssToClock, txUpdated } from './stand.js'

const NO_GPU_ARGS = ['--disable-gpu', '--disable-accelerated-2d-canvas', '--disable-gpu-compositing']

test('the stand runs the user branches: no test mode, navigator.webdriver is false, the quality autoguard has put a no-GPU browser on low', async ({ page }) => {
  const mock = await boot(page, { snapshot: ringSnapshot(), clock: false })
  const flags = await page.evaluate(() => {
    const g = (window as unknown as { __geoSim: { isTestMode: boolean; isWebDriver: boolean } }).__geoSim
    const root = document.querySelector<HTMLElement>('.root')!
    const fx = document.querySelector<HTMLElement>('canvas.canvas-fx')!
    const base = document.querySelector<HTMLElement>('canvas.canvas:not(.canvas-fx)')!
    return {
      isTestMode: g.isTestMode, isWebDriver: g.isWebDriver, navigatorWebdriver: navigator.webdriver,
      dataWebdriver: root.dataset.webdriver, quality: root.dataset.quality, theme: root.dataset.theme,
      fxZ: getComputedStyle(fx).zIndex, baseZ: getComputedStyle(base).zIndex,
    }
  })
  console.log('[038 probe] flags', JSON.stringify(flags), 'unmocked', JSON.stringify([...new Set(mock.unmocked)]))
  expect(flags).toMatchObject({ isTestMode: false, isWebDriver: false, navigatorWebdriver: false, dataWebdriver: '0' })
  // The autoguard is off under WebDriver (useQualityAutoguards.ts:83) and the default is 'med'; 'low' is its doing.
  expect(flags.quality, 'the autoguard saw software rendering and lowered the quality').toBe('low')
  expect(Number(flags.fxZ), 'the FX canvas is above the base canvas, as for a user').toBeGreaterThan(Number(flags.baseZ) || 0)
})

test('page.clock drives the render loop and the JS TTL, and does NOT drive the CSS animation of a label', async ({ page }) => {
  await boot(page, { snapshot: ringSnapshot(), clock: true })

  // Render loop: no draw while the clock stands, draws when it runs.
  const c0 = await loopCounters(page)
  await pushEvent(page, txUpdated(['a', 'b']))
  const c1 = await loopCounters(page)
  await frames(page, 5)
  const c2 = await loopCounters(page)
  expect(c1.draws - c0.draws, 'no frame is drawn while the clock stands').toBe(0)
  expect(c2.draws - c1.draws, 'five 16 ms steps draw five frames').toBe(5)

  // CSS animation: undo the stand's own pause, then watch it run while the JS clock stands still.
  const read = () => page.evaluate(() => {
    const el = document.querySelector<HTMLElement>('.floating-label-inner')!
    const a = el.getAnimations()[0]!
    return { currentTime: Number(a.currentTime), js: performance.now(), state: a.playState }
  })
  await page.evaluate(() => document.querySelector<HTMLElement>('.floating-label-inner')!.getAnimations()[0]!.play())
  const a0 = await read()
  await expect.poll(async () => (await read()).currentTime - a0.currentTime, { timeout: 10_000 }).toBeGreaterThan(300)
  const a1 = await read()
  console.log('[038 probe] css vs clock', JSON.stringify({ a0, a1 }))
  expect(a1.js, 'the JS clock did not move').toBe(a0.js)
  expect(a1.currentTime - a0.currentTime, 'the CSS animation moved by itself').toBeGreaterThan(300)

  // The working way: pause + currentTime from the JS clock.
  const s = await syncCssToClock(page)
  expect(s[0]!.ageMs).toBe(80)
  const again = await syncCssToClock(page)
  expect(again[0]!.opacity, 'the same JS time gives the same opacity').toBe(s[0]!.opacity)

  // JS TTL of the label: 1900 ms of the fake clock, however much wall time went by above.
  await page.clock.runFor(1900 - 80 - 16)
  expect((await fxCounts(page)).labels, 'sender label still mounted one frame before its TTL').toBeGreaterThanOrEqual(1)
  const before = (await syncCssToClock(page)).filter((l) => l.text.startsWith('-')).length
  await frames(page, 2)
  const after = (await syncCssToClock(page)).filter((l) => l.text.startsWith('-')).length
  expect([before, after], 'the sender label is removed by the JS TTL on the fake clock').toEqual([1, 0])
})

// The S0 probe "the spark is on the composed screen for a user" looked at one frame and held on 7 pixels (adversarial
// pass, F3). It is replaced by property 5 of the motion oracle (`motion.qa.ts`, "oracle - a payment over one edge"),
// which compares buffer and screenshot on every frame along the edge. What stays here is the other side of that
// property: a browser in which the buffer is full and the page shows nothing. If this test goes green on a visible
// beam, the oracle has stopped telling visible from invisible.
test('under WebDriver without test mode the FX buffer holds the beam and the composed page does not show it', async ({ baseURL }) => {
  // The default Playwright browser: navigator.webdriver === true. Test mode is off, as everywhere on this stand.
  const browser = await chromium.launch({ args: NO_GPU_ARGS })
  try {
    const page = await browser.newPage({ baseURL, viewport: { width: 1280, height: 720 }, deviceScaleFactor: 1 })
    await boot(page, { snapshot: ringSnapshot(), clock: true, allowWebdriver: true })
    const flags = await page.evaluate(() => {
      const root = document.querySelector<HTMLElement>('.root')!
      return {
        navigatorWebdriver: navigator.webdriver, dataWebdriver: root.dataset.webdriver, quality: root.dataset.quality,
        fxZ: getComputedStyle(document.querySelector('canvas.canvas-fx')!).zIndex,
        baseZ: getComputedStyle(document.querySelector('canvas.canvas:not(.canvas-fx)')!).zIndex,
      }
    })
    const g = await pickRingRoute(page, RING, 1)
    await rememberBaseline(page, g)
    await pushEvent(page, txUpdated(g.ids, '10.00', { amount_flyout: false }))
    const [edge] = summarize(g, await watchFlight(page, g, 30, 1))
    const fx = await fxCounts(page)
    console.log('[038 probe] webdriver browser', JSON.stringify({ flags, edge: { ...edge, litFrames: edge!.litFrames.length }, fx }))
    expect(flags).toMatchObject({ navigatorWebdriver: true, dataWebdriver: '1' })
    expect(fx.sparks, 'the spark is spawned: spawning depends on test mode, not on WebDriver').toBe(1)
    expect(edge!.framesStrong, 'the FX buffer holds the beam in many frames').toBeGreaterThanOrEqual(3)
    expect(edge!.strongPx, 'and in many pixels').toBeGreaterThan(20)
    expect({ framesShown: edge!.framesShown, shownPx: edge!.shownPx }, 'App.css:49-51 puts the FX canvas under the opaque base canvas: nothing on the page').toEqual({ framesShown: 0, shownPx: 0 })
  } finally {
    await browser.close()
  }
})

test('event entry through page.route: a fulfilled SSE body ends, the product reconnects with Last-Event-ID, the event is animated once', async ({ page }) => {
  // The alternative to the in-page stream of `installSseShim`: the backend's replay contract on `page.route`.
  // `route.fulfill` cannot keep a stream open, so every answer is a finished body.
  const eventId = `evt_${RUN_ID}_900001`
  const body = JSON.stringify({ event_id: eventId, ts: '2026-02-01T00:00:01.000Z', equivalent: EQ, ...txUpdated(['a', 'b']) })
  const frame = `id: ${eventId}\nevent: tx.updated\ndata: ${body}\n\n`
  const connections: Array<{ lastEventId: string | null; sent: boolean }> = []
  let armed = false
  await boot(page, {
    snapshot: ringSnapshot(), clock: true, sseShim: false,
    beforeLoad: (p) => p.route(new RegExp(`/simulator/runs/${RUN_ID}/events`, 'i'), (r) => {
      const lastEventId = r.request().headers()['last-event-id'] ?? null
      const send = armed && lastEventId !== eventId
      connections.push({ lastEventId, sent: send })
      return r.fulfill({ status: 200, headers: { 'content-type': 'text/event-stream; charset=utf-8', 'cache-control': 'no-cache' }, body: send ? `:ok\n\n${frame}` : ':ok\n\n' })
    }),
  })

  const sparksSeen: number[] = []
  const stepUntil = async (done: () => boolean, maxSteps: number) => {
    for (let i = 0; i < maxSteps && !done(); i += 1) {
      await page.clock.runFor(250)
      sparksSeen.push((await fxCounts(page)).sparks)
    }
  }
  await stepUntil(() => connections.length >= 2, 200)
  const before = connections.length
  armed = true
  await stepUntil(() => connections.some((c) => c.sent) && connections[connections.length - 1]!.lastEventId === eventId, 400)
  const fakeMsToDeliver = sparksSeen.length * 250
  await stepUntil(() => false, 40) // 10 s more: does a later reconnect animate the event again?
  const row = {
    connectionsBeforeArming: before, connections: connections.length, sent: connections.filter((c) => c.sent).length,
    lastEventIdEchoed: connections.some((c) => c.lastEventId === eventId), maxSparks: Math.max(0, ...sparksSeen),
    stepsWithASpark: sparksSeen.filter((n) => n > 0).length, fakeMsSteppedUntilEcho: fakeMsToDeliver,
  }
  console.log('[038 probe] page.route SSE', JSON.stringify(row))
  expect(row.sent, 'the mock sent the event on exactly one connection').toBe(1)
  expect(row.lastEventIdEchoed, 'the product reconnected with Last-Event-ID of the event').toBe(true)
  expect(row.maxSparks, 'the event reached the FX through the pipeline').toBe(1)
})
