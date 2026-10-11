/**
 * 038 S0 - probes of the stand itself: what `page.clock` drives and what it does not, whether the FX canvas can be
 * read on software-only Chromium, and what a WebDriver browser shows on screen against what its FX buffer holds.
 * Characterizations of the product on `e2dc3b2a`; none of them is a defect reproducer.
 */
import { chromium, expect, test, type Page } from '@playwright/test'

import {
  EQ, RUN_ID, boot, frames, fxCounts, loopCounters, pointsAlong, pushEvent, ringSnapshot, sampleCanvas, screenNodes, syncCssToClock,
  txUpdated, type Pt,
} from './stand.js'

const NO_GPU_ARGS = ['--disable-gpu', '--disable-accelerated-2d-canvas', '--disable-gpu-compositing']

/** Pixels of a screenshot clip that differ from the same clip taken earlier. A screenshot is the composed page, as a user sees it. */
async function screenshotDiff(page: Page, before: Buffer, after: Buffer): Promise<{ changed: number; total: number }> {
  return page.evaluate(async ([a, b]) => {
    const px = async (b64: string) => {
      const bmp = await createImageBitmap(await (await fetch(`data:image/png;base64,${b64}`)).blob())
      const c = new OffscreenCanvas(bmp.width, bmp.height)
      const ctx = c.getContext('2d')!
      ctx.drawImage(bmp, 0, 0)
      return ctx.getImageData(0, 0, bmp.width, bmp.height).data
    }
    const [pa, pb] = [await px(a!), await px(b!)]
    let changed = 0
    for (let i = 0; i < pa.length; i += 4) {
      if (Math.abs(pa[i]! - pb[i]!) + Math.abs(pa[i + 1]! - pb[i + 1]!) + Math.abs(pa[i + 2]! - pb[i + 2]!) > 24) changed += 1
    }
    return { changed, total: pa.length / 4 }
  }, [before.toString('base64'), after.toString('base64')])
}

function clipAround(points: Pt[]) {
  const xs = points.map((p) => p.x)
  const ys = points.map((p) => p.y)
  const x = Math.floor(Math.min(...xs)) - 6
  const y = Math.floor(Math.min(...ys)) - 6
  return { x, y, width: Math.ceil(Math.max(...xs)) + 6 - x, height: Math.ceil(Math.max(...ys)) + 6 - y }
}

/** The beam of a 3-edge payment is drawn on the chord a -> d (F-038-3); the probe only needs "some FX at a known place". */
async function sparkOnChord(page: Page) {
  const nodes = await screenNodes(page)
  const chord = pointsAlong(nodes.get('a')!, nodes.get('d')!, { endMarginPx: 40 })
  const clip = clipAround(chord)
  const shotBefore = await page.screenshot({ clip })
  const bufferBefore = await sampleCanvas(page, { chord })
  await pushEvent(page, txUpdated(['a', 'b', 'c', 'd']))
  await frames(page, 25) // 400 ms of a 1200 ms beam
  const bufferAfter = await sampleCanvas(page, { chord })
  const shotAfter = await page.screenshot({ clip })
  return { bufferBefore, bufferAfter, screen: await screenshotDiff(page, shotBefore, shotAfter), fx: await fxCounts(page) }
}

test('the stand runs the user branches: no test mode, navigator.webdriver is false, quality autoguard is live', async ({ page }) => {
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

test('the FX canvas is readable on software-only Chromium, and the spark is on the composed screen for a user', async ({ page }) => {
  await boot(page, { snapshot: ringSnapshot(), clock: true })
  const r = await sparkOnChord(page)
  console.log('[038 probe] user browser', JSON.stringify(r))
  expect(r.bufferBefore.__canvas.lit, 'counter-check: nothing is painted on the FX canvas before the event').toBe(0)
  expect(r.fx.sparks).toBe(1)
  expect(r.bufferAfter.chord.lit, 'the FX buffer holds the beam').toBeGreaterThan(0)
  expect(r.screen.changed, 'the composed screenshot shows the beam').toBeGreaterThan(0)
})

test('under WebDriver without test mode the FX buffer holds the spark and the composed screen does not show it', async ({ baseURL }) => {
  // The default Playwright browser: navigator.webdriver === true. Test mode is off, as everywhere on this stand.
  const browser = await chromium.launch({ args: NO_GPU_ARGS })
  try {
    const page = await browser.newPage({ baseURL, viewport: { width: 1280, height: 720 }, deviceScaleFactor: 1 })
    await boot(page, { snapshot: ringSnapshot(), clock: true })
    const flags = await page.evaluate(() => {
      const root = document.querySelector<HTMLElement>('.root')!
      return {
        navigatorWebdriver: navigator.webdriver, dataWebdriver: root.dataset.webdriver, quality: root.dataset.quality,
        fxZ: getComputedStyle(document.querySelector('canvas.canvas-fx')!).zIndex,
        baseZ: getComputedStyle(document.querySelector('canvas.canvas:not(.canvas-fx)')!).zIndex,
      }
    })
    const r = await sparkOnChord(page)
    console.log('[038 probe] webdriver browser', JSON.stringify({ flags, ...r }))
    expect(flags).toMatchObject({ navigatorWebdriver: true, dataWebdriver: '1' })
    expect(r.fx.sparks, 'the spark is spawned: spawning depends on test mode, not on WebDriver').toBe(1)
    expect(r.bufferAfter.chord.lit, 'the FX buffer holds the beam').toBeGreaterThan(0)
    expect(r.screen.changed, 'App.css:49-51 puts the FX canvas under the opaque base canvas: nothing on screen').toBe(0)
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
